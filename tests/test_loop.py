from pathlib import Path
from typing import Any
from sql_agent.agent.loop import ReActSQLAgent
from sql_agent.config import Settings
from sql_agent.llm import LLMResponse, ToolCallRequest
from sql_agent.schemas import TerminationReason
from sql_agent.tools.db_tools import DatabaseToolkit
from sql_agent.tools.registry import build_default_registry


class FakeLLMClient:
    def __init__(self, scripted_responses: list[LLMResponse]) -> None:
        self._responses = list(scripted_responses)
        self.calls_made = 0

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        self.calls_made += 1
        return self._responses.pop(0)


def _make_settings(tmp_path: Path, max_steps: int = 5) -> Settings:
    return Settings(
        groq_api_key="test-key",
        db_path=Path("data/analytics.db"),
        logs_dir=tmp_path / "logs",
        max_steps=max_steps,
    )


def test_agent_self_corrects_after_sql_error(tmp_path: Path):
    fake_llm = FakeLLMClient([
        # Step 1: Model makes a bad SQL query with a non-existent column
        LLMResponse(
            content=None,
            reasoning="Let me query revenue directly.",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="execute_read_only_sql",
                    arguments='{"query": "SELECT SUM(non_existent_col) FROM ord_hdr"}',
                )
            ],
            prompt_tokens=100,
            completion_tokens=30,
        ),
        # Step 2: Model reads the SQL error observation and fixes the column name
        LLMResponse(
            content=None,
            reasoning="That column does not exist. Let me use gross_cents.",
            tool_calls=[
                ToolCallRequest(
                    id="call_2",
                    name="execute_read_only_sql",
                    arguments='{"query": "SELECT COUNT(*) AS cnt FROM ord_hdr"}',
                )
            ],
            prompt_tokens=150,
            completion_tokens=35,
        ),
        # Step 3: Model returns final answer
        LLMResponse(
            content="There are 1,500 total orders in the database.",
            reasoning="I have the exact count now.",
            tool_calls=[],
            prompt_tokens=180,
            completion_tokens=20,
        ),
    ])

    toolkit = DatabaseToolkit(Path("data/analytics.db"))
    agent = ReActSQLAgent(
        llm=fake_llm,
        registry=build_default_registry(toolkit),
        settings=_make_settings(tmp_path),
        verbose=False,
    )

    result = agent.run("How many orders exist?")
    assert result.termination_reason == TerminationReason.COMPLETED
    assert result.total_steps == 3
    assert result.traces[0].tool_result is not None
    assert result.traces[0].tool_result.success is False
    assert result.traces[1].tool_result is not None
    assert result.traces[1].tool_result.success is True
    assert "1,500" in result.final_answer


def test_agent_terminates_on_duplicate_loop(tmp_path: Path):
    repeated_call = LLMResponse(
        content=None,
        reasoning="Listing tables again.",
        tool_calls=[ToolCallRequest(id="call_dup", name="list_tables", arguments="{}")],
    )
    fake_llm = FakeLLMClient([repeated_call, repeated_call, repeated_call])

    toolkit = DatabaseToolkit(Path("data/analytics.db"))
    agent = ReActSQLAgent(
        llm=fake_llm,
        registry=build_default_registry(toolkit),
        settings=_make_settings(tmp_path, max_steps=6),
        verbose=False,
    )

    result = agent.run("Show tables")
    assert result.termination_reason == TerminationReason.DUPLICATE_ACTION_LOOP
    assert result.total_steps == 2  # Aborts on the 3rd attempt before recording a 3rd completed step


def test_agent_forces_final_answer_when_max_steps_reached(tmp_path: Path):
    fake_llm = FakeLLMClient([
        LLMResponse(
            content=None,
            reasoning="Step 1",
            tool_calls=[ToolCallRequest(id="c1", name="list_tables", arguments="{}")],
        ),
        LLMResponse(
            content=None,
            reasoning="Step 2",
            tool_calls=[
                ToolCallRequest(
                    id="c2",
                    name="describe_table",
                    arguments='{"table_name": "ord_hdr"}',
                )
            ],
        ),
        # Forced final synthesis call (tools=None)
        LLMResponse(
            content="Partial answer synthesized at step limit.",
            reasoning="Summarizing available findings.",
            tool_calls=[],
        ),
    ])

    toolkit = DatabaseToolkit(Path("data/analytics.db"))
    agent = ReActSQLAgent(
        llm=fake_llm,
        registry=build_default_registry(toolkit),
        settings=_make_settings(tmp_path, max_steps=2),
        verbose=False,
    )

    result = agent.run("Complex multi-step query")
    assert result.termination_reason == TerminationReason.MAX_STEPS_REACHED
    assert "Partial answer" in result.final_answer