import sys
from sql_agent.agent.loop import ReActSQLAgent
from sql_agent.config import get_settings
from sql_agent.llm import GroqLLMClient
from sql_agent.tools.db_tools import DatabaseToolkit
from sql_agent.tools.registry import build_default_registry


def build_agent(verbose: bool = True) -> ReActSQLAgent:
    settings = get_settings()
    toolkit = DatabaseToolkit(
        db_path=settings.db_path,
        max_rows=settings.max_rows_returned,
        timeout_sec=settings.sql_timeout_sec,
    )
    registry = build_default_registry(toolkit)
    llm = GroqLLMClient(settings=settings)
    return ReActSQLAgent(llm=llm, registry=registry, settings=settings, verbose=verbose)


def main() -> None:
    default_question = (
        "Why did net revenue in the West region drop in Q3 2026 compared to Q2 2026? "
        "Quantify the drop and identify the primary root cause."
    )
    question = " ".join(sys.argv[1:]).strip() or default_question

    print(f"Question: {question}")
    agent = build_agent(verbose=True)
    result = agent.run(question)

    print("\n" + "=" * 70)
    print("FINAL ANSWER")
    print("=" * 70)
    print(result.final_answer)
    print("\n" + "-" * 70)
    print(
        f"Run ID: {result.run_id} | Status: {result.termination_reason.value} | "
        f"Steps: {result.total_steps} | Tokens: {result.total_prompt_tokens + result.total_completion_tokens} | "
        f"Latency: {result.total_latency_ms}ms | Est. Cost: ${result.estimated_cost_usd:.6f}"
    )


if __name__ == "__main__":
    main()