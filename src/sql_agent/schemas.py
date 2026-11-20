from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    MAX_STEPS_REACHED = "max_steps_reached"
    DUPLICATE_ACTION_LOOP = "duplicate_action_loop"
    MAX_CONSECUTIVE_ERRORS = "max_consecutive_errors"
    LLM_API_ERROR = "llm_api_error"


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    output: str
    error_type: str | None = None
    execution_ms: float = 0.0
    truncated: bool = False


class StepTrace(BaseModel):
    step_number: int
    reasoning: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: ToolResult | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_latency_ms: float = 0.0


class AgentRunResult(BaseModel):
    run_id: str
    question: str
    final_answer: str
    termination_reason: TerminationReason
    total_steps: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_latency_ms: float
    estimated_cost_usd: float
    traces: list[StepTrace] = Field(default_factory=list)