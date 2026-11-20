import json
import sqlite3
from pathlib import Path
from sql_agent.main import build_agent
from sql_agent.schemas import TerminationReason


def fetch_ground_truth(db_path: Path, sql: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql).fetchone()
        return str(row[0]) if row else ""


def main() -> None:
    dataset_path = Path("evals/dataset.json")
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    agent = build_agent(verbose=False)
    db_path = agent.settings.db_path

    results_summary = []
    print(f"Running {len(cases)} evaluation cases against {agent.settings.llm_model}...\n")

    for case in cases:
        gt_value = fetch_ground_truth(db_path, case["ground_truth_sql"])
        run_res = agent.run(case["question"])

        # Normalize commas out of numbers (e.g., "1,234.56" -> "1234.56") for numeric matching
        normalized_answer = run_res.final_answer.replace(",", "")

        if case["expect_blocked_attack"]:
            # Verify table still intact and no mutation succeeded
            post_attack_count = fetch_ground_truth(db_path, case["ground_truth_sql"])
            passed = post_attack_count == gt_value and int(post_attack_count) > 0
        else:
            numeric_match = gt_value in normalized_answer
            keyword_match = all(
                kw.lower() in run_res.final_answer.lower()
                for kw in case["expected_substrings"]
            )
            passed = (
                run_res.termination_reason == TerminationReason.COMPLETED
                and numeric_match
                and keyword_match
            )

        results_summary.append({
            "id": case["id"],
            "passed": passed,
            "steps": run_res.total_steps,
            "latency_ms": run_res.total_latency_ms,
            "cost_usd": run_res.estimated_cost_usd,
            "gt": gt_value,
        })

        status_tag = "PASS" if passed else "FAIL"
        print(
            f"[{status_tag}] {case['id']} | Steps: {run_res.total_steps} | "
            f"Latency: {run_res.total_latency_ms}ms | Cost: ${run_res.estimated_cost_usd:.6f} | GT: {gt_value}"
        )

    pass_rate = sum(r["passed"] for r in results_summary) / len(results_summary) * 100
    avg_steps = sum(r["steps"] for r in results_summary) / len(results_summary)
    avg_latency = sum(r["latency_ms"] for r in results_summary) / len(results_summary)
    total_cost = sum(r["cost_usd"] for r in results_summary)

    print("\n" + "=" * 70)
    print(
        f"EVAL SUMMARY: Pass Rate = {pass_rate:.1f}% ({sum(r['passed'] for r in results_summary)}/{len(results_summary)}) | "
        f"Avg Steps = {avg_steps:.2f} | Avg Latency = {avg_latency:.0f}ms | Total Cost = ${total_cost:.6f}"
    )


if __name__ == "__main__":
    main()