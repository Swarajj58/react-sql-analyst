import time
from dataclasses import dataclass, field
from typing import Any
from groq import APIConnectionError, APIStatusError, Groq, RateLimitError
from sql_agent.config import Settings


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # Raw JSON string emitted by the model


@dataclass
class LLMResponse:
    content: str | None
    reasoning: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


class GroqLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.client = Groq(
            api_key=settings.groq_api_key,
            timeout=settings.llm_timeout_sec,
        )
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        max_retries: int = 3,
    ) -> LLMResponse:
        """Calls Groq Chat Completions with exponential backoff on transient errors."""
        backoff = 1.0
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            start = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice

                completion = self.client.chat.completions.create(**kwargs)
                latency_ms = round((time.perf_counter() - start) * 1000, 2)

                choice = completion.choices[0].message
                parsed_calls = [
                    ToolCallRequest(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments or "{}",
                    )
                    for tc in (choice.tool_calls or [])
                ]

                usage = completion.usage
                return LLMResponse(
                    content=choice.content,
                    reasoning=getattr(choice, "reasoning", None),
                    tool_calls=parsed_calls,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    latency_ms=latency_ms,
                )

            except (RateLimitError, APIConnectionError) as exc:
                last_error = exc
                if attempt == max_retries:
                    break
                time.sleep(backoff)
                backoff *= 2.0
            except APIStatusError as exc:
                # Retry 5xx server errors; fail immediately on 4xx client errors (except 429)
                if exc.status_code >= 500 and attempt < max_retries:
                    last_error = exc
                    time.sleep(backoff)
                    backoff *= 2.0
                else:
                    raise

        raise RuntimeError(f"Groq API failed after {max_retries} attempts: {last_error}")