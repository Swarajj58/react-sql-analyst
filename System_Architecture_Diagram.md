
# ReAct Agent Architecture

```text
+-----------------------------------------------------------------------------------+
|                                  CLI / Eval Harness                               |
|                                (main.py / run_eval.py)                            |
+-----------------------------------------+-----------------------------------------+
                                          | User Question
                                          v
+-----------------------------------------------------------------------------------+
|                            ReAct Orchestrator (loop.py)                           |
|  - Manages AgentState (messages, step_count, consecutive_errors, call_history)    |
|  - Enforces termination guards (max_steps, loop detection, terminal errors)       |
+-------------------+---------------------------------------------+-----------------+
                    | Messages + Tool Schemas                     ^ Formatted
                    v                                             | Observation
+---------------------------------------+         +---------------------------------+
|         LLM Client (llm.py)           |         |     Context Manager (memory.py) |
| - Calls Groq (openai/gpt-oss-20b)     |         | - Truncates rows (>50) & chars  |
| - Retries transient API 429/5xx       |         | - Appends truncation metadata   |
| - Captures token & latency metrics    |         +---------------------------------+
+-------------------+-------------------+                         ^
                    | Assistant Message                           | Raw ToolResult
                    | (Reasoning + tool_calls)                    |
                    v                                             |
+-----------------------------------------------------------------------------------+
|                        Tool Registry & Dispatcher (registry.py)                   |
|  - Validates tool name exists                                                     |
|  - Validates JSON arguments against Pydantic input schemas                        |
|  - Catches exceptions & converts them into structured error observations          |
+-----------------------------------------+-----------------------------------------+
                                          | Validated Args
                                          v
+-----------------------------------------------------------------------------------+
|                          Sandboxed Tool Layer (db_tools.py)                       |
|  +-----------------+  +------------------+  +-------------------+  +-----------+  |
|  |  list_tables()  |  | describe_table() |  | distinct_values() |  | run_sql() |  |
|  +-----------------+  +------------------+  +-------------------+  +-----+-----+  |
+--------------------------------------------------------------------------|--------+
                                                                           |
                                                                           v
                                                          +-------------------------+
                                                          | SQL AST Guard           |
                                                          | (sql_guard.py)          |
                                                          | - Parses AST (sqlglot)  |
                                                          | - Blocks DDL/DML/PRAGMA |
                                                          | - Auto-injects LIMIT    |
                                                          +------------+------------+
                                                                       | Safe SELECT
                                                                       v
                                                          +-------------------------+
                                                          | SQLite (Read-Only URI)  |
                                                          | file:analytics.db?mode=ro|
                                                          +-------------------------+
```
