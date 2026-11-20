```text
config.py & schemas.py (Data Contracts)

Define strict Pydantic models (AgentState, StepTrace, ToolResult, AgentRunResult) so no raw untyped dictionaries float through the system.

llm.py (Model Interface)

Wraps the Groq Python SDK (client.chat.completions.create).

Important openai/gpt-oss-20b detail: gpt-oss-20b is a reasoning MoE model trained with OpenAI's Harmony format—it produces internal chain-of-thought reasoning alongside native tool_calls. Our wrapper extracts the reasoning/content, tool call IDs, function names, and token usage cleanly without exposing internal reasoning leakage to downstream tools.


tools/registry.py (The Execution Firewall)

The LLM never executes Python functions directly. It emits a JSON request (name + arguments).

The registry validates that the tool exists, parses the JSON string safely (catching malformed JSON), validates types via Pydantic, and runs the tool inside a try/except block so crashes become observations, not unhandled exceptions.

tools/sql_guard.py + Read-Only SQLite (Defense-in-Depth)

Layer 1 (AST Validation): Uses sqlglot to parse the query into an Abstract Syntax Tree. We reject multi-statement queries (SELECT 1; DROP TABLE orders), non-SELECT root nodes (INSERT, UPDATE, DELETE, ALTER, DROP, PRAGMA, ATTACH), and unparseable fallback exp.Command nodes. If a SELECT lacks a LIMIT, the AST rewriter injects LIMIT 50 automatically.

Layer 2 (OS/DB-Level Lock): Even if an AST check had a zero-day bypass, we open SQLite using sqlite3.connect("file:data/analytics.db?mode=ro", uri=True). The SQLite engine itself physically refuses writes.

agent/memory.py (Context Window Protection)

If a query returns 50 rows of wide text, dumping it raw into messages wastes tokens and degrades attention. memory.py caps serialized tool output at a strict character/row budget and appends a deterministic hint (e.g., "[TRUNCATED: Showing 50 of 412 rows. Use GROUP BY or tighter WHERE filters.]").

agent/loop.py (The State Machine)

Drives the while loop, checks stopping conditions before every LLM call, updates AgentState, and emits structured telemetry to observability/tracer.py.

3. Native Tool Calling vs. Text-Parsed ReAct (Crucial Interview Distinction)In early ReAct papers (2022), engineers prompted models to output raw text strings (Thought: ... Action: run_sql[...]) and parsed them with brittle regular expressions.In modern production systems with models like openai/gpt-oss-20b, we use Native Tool Calling via the Chat Completions message protocol:   System + User Message enter messages.LLM Turn (role: "assistant"): The model reasons internally and returns tool_calls=[{id: "call_1", function: {name: "list_tables", arguments: "{}"}}].Tool Execution (role: "tool"): We execute the tool and append {role: "tool", tool_call_id: "call_1", content: "..."} to messages.Loop Continues: The model reads the role: "tool" observation, reasons about what to do next, and either emits another tool_call or returns a final role: "assistant" message with tool_calls=None.Why this matters in interviews: Native tool calling enforces schema adherence at the decoding/API layer, eliminates regex parsing bugs when SQL queries contain quotes or newlines, and natively correlates parallel or sequential tool_call_ids.4. Termination Conditions (How We Prevent Infinite Loops)An unconstrained while True: loop is a production outage waiting to happen. Our loop.py enforces 4 deterministic exit gates:Natural Completion: assistant_message.tool_calls is empty/None → return final answer.Hard Step Budget (max_steps = 8): Prevents runaway token spend on ambiguous questions. If hit, we force one final synthesis call without tools (tool_choice="none") so the user gets a graceful partial answer rather than a crash.Duplicate Action Detection: We hash (tool_name, normalized_arguments). If the agent calls the exact same tool with the exact same arguments twice in a row (a classic small-model failure mode), we inject a system nudge: "You already executed this exact query and got the result above. Do not repeat it; either adjust your query or conclude." If it repeats a 3rd time, terminate.Consecutive Tool Error Limit (max_consecutive_errors = 3): If the agent writes broken SQL 3 times consecutively without making progress, abort safely.5. Ready for Phase 3: Incremental Implementation

```