from sql_agent.schemas import ToolResult


class ContextManager:
    """Protects the LLM context window from oversized tool observations."""

    def __init__(self, max_observation_chars: int = 2500) -> None:
        self.max_observation_chars = max_observation_chars

    def format_observation(self, result: ToolResult) -> ToolResult:
        if len(result.output) <= self.max_observation_chars:
            return result

        clipped = result.output[: self.max_observation_chars]
        notice = (
            f"\n... [OBSERVATION TRUNCATED at {self.max_observation_chars} chars "
            f"from {len(result.output)} total chars. Use tighter WHERE filters or GROUP BY aggregations.]"
        )
        return result.model_copy(
            update={
                "output": clipped + notice,
                "truncated": True,
            }
        )