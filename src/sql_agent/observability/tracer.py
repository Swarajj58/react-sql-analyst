import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sql_agent.schemas import AgentRunResult, StepTrace


class RunTracer:
    """Records step-by-step ReAct execution telemetry and persists runs to JSONL."""

    # Groq pricing for openai/gpt-oss-20b (USD per 1M tokens)
    INPUT_COST_PER_M = 0.075
    OUTPUT_COST_PER_M = 0.30

    def __init__(self, question: str, logs_dir: Path) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.question = question
        self.logs_dir = logs_dir
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.traces: list[StepTrace] = []

    def record_step(self, trace: StepTrace) -> None:
        self.traces.append(trace)

    def compute_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        input_cost = (prompt_tokens / 1_000_000) * self.INPUT_COST_PER_M
        output_cost = (completion_tokens / 1_000_000) * self.OUTPUT_COST_PER_M
        return round(input_cost + output_cost, 6)

    def finalize(
        self,
        final_answer: str,
        termination_reason,
        total_latency_ms: float,
    ) -> AgentRunResult:
        total_prompt = sum(t.prompt_tokens for t in self.traces)
        total_completion = sum(t.completion_tokens for t in self.traces)
        cost = self.compute_cost_usd(total_prompt, total_completion)

        result = AgentRunResult(
            run_id=self.run_id,
            question=self.question,
            final_answer=final_answer,
            termination_reason=termination_reason,
            total_steps=len(self.traces),
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            total_latency_ms=round(total_latency_ms, 2),
            estimated_cost_usd=cost,
            traces=self.traces,
        )
        self._persist(result)
        return result

    def _persist(self, result: AgentRunResult) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.logs_dir / f"run_{datetime.now(timezone.utc):%Y%m%d}.jsonl"
        payload = {
            "timestamp": self.started_at,
            **result.model_dump(),
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")