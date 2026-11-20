import json
import time
from typing import Any, Protocol
from sql_agent.agent.memory import ContextManager
from sql_agent.config import Settings
from sql_agent.llm import LLMResponse
from sql_agent.observability.tracer import RunTracer
from sql_agent.prompts import FORCE_FINAL_ANSWER_PROMPT, SYSTEM_PROMPT
from sql_agent.schemas import AgentRunResult, StepTrace, TerminationReason
from sql_agent.tools.registry import ToolRegistry


class LLMProtocol(Protocol):
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse: ...


class ReActSQLAgent:
    def __init__(
        self,
        llm: LLMProtocol,
        registry: ToolRegistry,
        settings: Settings,
        verbose: bool = True,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.settings = settings
        self.memory = ContextManager(max_observation_chars=settings.max_observation_chars)
        self.verbose = verbose

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    @staticmethod
    def _action_fingerprint(tool_name: str, raw_args: str) -> str:
        try:
            normalized = json.dumps(json.loads(raw_args), sort_keys=True)
        except json.JSONDecodeError:
            normalized = raw_args.strip()
        return f"{tool_name}::{normalized}"

    def run(self, question: str) -> AgentRunResult:
        run_start = time.perf_counter()
        tracer = RunTracer(question=question, logs_dir=self.settings.logs_dir)
        tools_schema = self.registry.to_openai_tools()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        seen_fingerprints: dict[str, int] = {}
        consecutive_errors = 0

        for step in range(1, self.settings.max_steps + 1):
            self._log(f"\n--- [Step {step}/{self.settings.max_steps}] ---")

            try:
                response = self.llm.generate(
                    messages=messages,
                    tools=tools_schema,
                    tool_choice="auto",
                )
            except Exception as exc:
                self._log(f"[Fatal LLM Error] {exc}")
                return tracer.finalize(
                    final_answer=f"Agent aborted due to LLM API failure: {exc}",
                    termination_reason=TerminationReason.LLM_API_ERROR,
                    total_latency_ms=(time.perf_counter() - run_start) * 1000,
                )

            if response.reasoning:
                self._log(f"Thought: {response.reasoning.strip()}")

            # Case 1: Model decides it has enough evidence and emits Final Answer
            if not response.tool_calls:
                final_text = (response.content or "").strip()
                tracer.record_step(
                    StepTrace(
                        step_number=step,
                        reasoning=response.reasoning,
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        llm_latency_ms=response.latency_ms,
                    )
                )
                self._log(f"Final Answer Ready ({response.latency_ms}ms)")
                return tracer.finalize(
                    final_answer=final_text,
                    termination_reason=TerminationReason.COMPLETED,
                    total_latency_ms=(time.perf_counter() - run_start) * 1000,
                )

            # Record assistant turn with tool_calls in conversation history
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # Execute each requested tool call (supports single or parallel tool calls)
            for idx, tc in enumerate(response.tool_calls):
                self._log(f"Action: {tc.name}({tc.arguments})")
                fp = self._action_fingerprint(tc.name, tc.arguments)
                seen_fingerprints[fp] = seen_fingerprints.get(fp, 0) + 1

                # Guard: Duplicate action loop detection
                if seen_fingerprints[fp] >= 3:
                    self._log("[Loop Guard] Agent repeated identical action 3 times. Aborting.")
                    return tracer.finalize(
                        final_answer="Agent terminated early because it entered a repetitive tool-call loop.",
                        termination_reason=TerminationReason.DUPLICATE_ACTION_LOOP,
                        total_latency_ms=(time.perf_counter() - run_start) * 1000,
                    )

                if seen_fingerprints[fp] == 2:
                    obs_text = (
                        "Warning: You already executed this exact tool call with identical arguments. "
                        "Review the earlier observation in context instead of repeating it. "
                        "Either modify your query or provide your final answer."
                    )
                    raw_result = self.registry.dispatch(tc.name, tc.arguments)
                    formatted_result = raw_result.model_copy(
                        update={"output": f"{obs_text}\n\nPrevious Output:\n{raw_result.output}"}
                    )
                else:
                    raw_result = self.registry.dispatch(tc.name, tc.arguments)
                    formatted_result = self.memory.format_observation(raw_result)

                # Track consecutive tool errors
                if formatted_result.success:
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1

                preview = formatted_result.output[:220].replace("\n", " ")
                self._log(
                    f"Observation [success={formatted_result.success}, {formatted_result.execution_ms}ms]: {preview}..."
                )

                # Append tool observation to message state
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": formatted_result.output,
                })

                # Record trace (attach LLM token usage to first tool call of the step)
                try:
                    parsed_args = json.loads(tc.arguments)
                except json.JSONDecodeError:
                    parsed_args = {"_raw": tc.arguments}

                tracer.record_step(
                    StepTrace(
                        step_number=step,
                        reasoning=response.reasoning if idx == 0 else None,
                        tool_name=tc.name,
                        tool_args=parsed_args,
                        tool_result=formatted_result,
                        prompt_tokens=response.prompt_tokens if idx == 0 else 0,
                        completion_tokens=response.completion_tokens if idx == 0 else 0,
                        llm_latency_ms=response.latency_ms if idx == 0 else 0.0,
                    )
                )

                if consecutive_errors >= self.settings.max_consecutive_errors:
                    self._log("[Error Guard] Max consecutive tool errors reached. Aborting.")
                    return tracer.finalize(
                        final_answer="Agent terminated because consecutive SQL/tool errors exceeded the safety threshold.",
                        termination_reason=TerminationReason.MAX_CONSECUTIVE_ERRORS,
                        total_latency_ms=(time.perf_counter() - run_start) * 1000,
                    )

        # Step budget exhausted: force one final synthesis turn without tools
        self._log("\n[Budget Guard] Max steps reached. Forcing final synthesis...")
        messages.append({"role": "user", "content": FORCE_FINAL_ANSWER_PROMPT})
        try:
            final_resp = self.llm.generate(messages=messages, tools=None)
            tracer.record_step(
                StepTrace(
                    step_number=self.settings.max_steps + 1,
                    reasoning=final_resp.reasoning,
                    prompt_tokens=final_resp.prompt_tokens,
                    completion_tokens=final_resp.completion_tokens,
                    llm_latency_ms=final_resp.latency_ms,
                )
            )
            return tracer.finalize(
                final_answer=(final_resp.content or "Unable to complete within step budget.").strip(),
                termination_reason=TerminationReason.MAX_STEPS_REACHED,
                total_latency_ms=(time.perf_counter() - run_start) * 1000,
            )
        except Exception as exc:
            return tracer.finalize(
                final_answer=f"Max steps reached and final synthesis failed: {exc}",
                termination_reason=TerminationReason.MAX_STEPS_REACHED,
                total_latency_ms=(time.perf_counter() - run_start) * 1000,
            )