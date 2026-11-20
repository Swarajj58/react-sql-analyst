import json
import sqlite3
from pathlib import Path
from pydantic import BaseModel, Field
from sql_agent.tools.sql_guard import validate_and_rewrite_sql


# --- Pydantic Input Schemas (Become LLM Tool JSON Schemas) ---

class EmptyArgs(BaseModel):
    pass


class DescribeTableArgs(BaseModel):
    table_name: str = Field(..., description="Exact name of the table to inspect.")


class DistinctValuesArgs(BaseModel):
    table_name: str = Field(..., description="Exact name of the table.")
    column_name: str = Field(..., description="Exact name of the categorical/status column.")
    limit: int = Field(default=20, ge=1, le=50, description="Max distinct values to return.")


class RunSQLArgs(BaseModel):
    query: str = Field(..., description="A single read-only SQLite SELECT query.")


# --- Database Tool Implementation ---

class DatabaseToolkit:
    def __init__(self, db_path: Path, max_rows: int = 50, timeout_sec: float = 5.0) -> None:
        self.db_path = db_path.resolve()
        self.max_rows = max_rows
        self.timeout_sec = timeout_sec

    def _connect_ro(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}")
        # Defense-in-depth: OS/SQLite-level read-only URI mode
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout_sec)
        conn.row_factory = sqlite3.Row
        return conn

    def _validate_identifier(self, conn: sqlite3.Connection, table_name: str) -> None:
        """Prevents SQL injection in metadata tools where identifiers can't be parameterized."""
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        )
        if cur.fetchone() is None:
            raise ValueError(f"Table '{table_name}' does not exist.")

    def list_tables(self) -> str:
        with self._connect_ro() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            tables = [r["name"] for r in rows]
            return json.dumps({"tables": tables})

    def describe_table(self, table_name: str) -> str:
        with self._connect_ro() as conn:
            self._validate_identifier(conn, table_name)
            cols = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            schema = [
                {
                    "column": c["name"],
                    "type": c["type"],
                    "notnull": bool(c["notnull"]),
                    "pk": bool(c["pk"]),
                }
                for c in cols
            ]
            sample_rows = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 3').fetchall()
            samples = [dict(r) for r in sample_rows]
            return json.dumps({
                "table": table_name,
                "columns": schema,
                "sample_rows": samples,
            }, default=str)

    def get_distinct_values(self, table_name: str, column_name: str, limit: int = 20) -> str:
        with self._connect_ro() as conn:
            self._validate_identifier(conn, table_name)
            cols = {r["name"] for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}
            if column_name not in cols:
                raise ValueError(
                    f"Column '{column_name}' not found in '{table_name}'. Available columns: {sorted(cols)}"
                )
            rows = conn.execute(
                f'SELECT DISTINCT "{column_name}" AS val, COUNT(*) AS cnt '
                f'FROM "{table_name}" GROUP BY "{column_name}" ORDER BY cnt DESC LIMIT ?',
                (limit,),
            ).fetchall()
            return json.dumps({
                "table": table_name,
                "column": column_name,
                "distinct_values": [dict(r) for r in rows],
            }, default=str)

    def execute_read_only_sql(self, query: str) -> str:
        safe_sql = validate_and_rewrite_sql(query, max_rows=self.max_rows)
        with self._connect_ro() as conn:
            rows = conn.execute(safe_sql).fetchall()
            records = [dict(r) for r in rows]
            return json.dumps({
                "executed_sql": safe_sql,
                "row_count": len(records),
                "rows": records,
            }, default=str)