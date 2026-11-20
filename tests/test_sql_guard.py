import pytest
from pathlib import Path
from sql_agent.tools.sql_guard import SQLSecurityError, validate_and_rewrite_sql
from sql_agent.tools.db_tools import DatabaseToolkit
from sql_agent.tools.registry import build_default_registry


def test_allows_valid_select_and_injects_limit():
    sql = validate_and_rewrite_sql("SELECT ord_id, gross_cents FROM ord_hdr", max_rows=25)
    assert "SELECT" in sql.upper()
    assert "LIMIT 25" in sql.upper()


def test_clamps_excessive_limit():
    sql = validate_and_rewrite_sql("SELECT * FROM ord_hdr LIMIT 5000", max_rows=50)
    assert "LIMIT 50" in sql.upper()
    assert "5000" not in sql


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "DROP TABLE ord_hdr",
        "DELETE FROM ord_hdr WHERE 1=1",
        "UPDATE cust_dim SET acct_tier = 'ENT_PRO'",
        "INSERT INTO refunds VALUES (999, 1, '2026-01-01', 100, 'HACK')",
        "SELECT * FROM ord_hdr; DROP TABLE ord_hdr;",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE 'evil.db' AS evil",
        "WITH x AS (DELETE FROM ord_hdr RETURNING *) SELECT * FROM x",
    ],
)
def test_blocks_malicious_or_mutating_sql(malicious_sql: str):
    with pytest.raises(SQLSecurityError):
        validate_and_rewrite_sql(malicious_sql)


def test_registry_catches_errors_gracefully():
    toolkit = DatabaseToolkit(Path("data/analytics.db"))
    registry = build_default_registry(toolkit)

    res = registry.dispatch("execute_read_only_sql", '{"query": "DROP TABLE ord_hdr"}')
    assert res.success is False
    assert res.error_type == "SQLSecurityError"
    assert "Security Policy Blocked Query" in res.output