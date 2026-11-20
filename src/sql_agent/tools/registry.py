import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable
from pydantic import BaseModel, ValidationError
from sql_agent.schemas import ToolResult
from sql_agent.tools.db_tools import (
    DatabaseToolkit,
    DescribeTableArgs,
    DistinctValuesArgs,
    EmptyArgs,
    RunSQLArgs,
)
from sql_agent.tools.sql_guard import SQLSecurityError


@dataclass
class RegisteredTool:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Exports registered tools in OpenAI/Groq function-calling schema format."""
        schemas = []
        for tool in self._tools.values():
            json_schema = tool.args_schema.model_json_schema()
            json_schema.pop("title", None)
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": json_schema,
                },
            })
        return schemas

    def dispatch(self, name: str, raw_arguments: str) -> ToolResult:
        start = time.perf_counter()

        if name not in self._tools:
            return ToolResult(
                tool_name=name,
                success=False,
                error_type="UnknownToolError",
                output=f"Error: Tool '{name}' does not exist. Available tools: {list(self._tools.keys())}",
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )

        tool = self._tools[name]

        # 1. Parse JSON arguments safely
        try:
            parsed_dict = json.loads(raw_arguments) if raw_arguments.strip() else {}
            if not isinstance(parsed_dict, dict):
                raise ValueError("Tool arguments must be a JSON object.")
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolResult(
                tool_name=name,
                success=False,
                error_type="ArgumentJSONError",
                output=f"Error: Invalid JSON arguments for '{name}': {exc}",
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )

        # 2. Validate types against Pydantic schema
        try:
            validated_args = tool.args_schema(**parsed_dict)
        except ValidationError as exc:
            return ToolResult(
                tool_name=name,
                success=False,
                error_type="ArgumentValidationError",
                output=f"Error: Invalid arguments for '{name}': {exc}",
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )

        # 3. Execute tool and translate domain errors into observations
        try:
            output = tool.handler(**validated_args.model_dump())
            return ToolResult(
                tool_name=name,
                success=True,
                output=output,
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except SQLSecurityError as exc:
            return ToolResult(
                tool_name=name,
                success=False,
                error_type="SQLSecurityError",
                output=f"Security Policy Blocked Query: {exc}",
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except (sqlite3.Error, ValueError, FileNotFoundError) as exc:
            return ToolResult(
                tool_name=name,
                success=False,
                error_type=type(exc).__name__,
                output=f"Database Tool Error ({type(exc).__name__}): {exc}",
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except Exception as exc:
            return ToolResult(
                tool_name=name,
                success=False,
                error_type="UnexpectedToolError",
                output=f"Unexpected execution error: {exc}",
                execution_ms=round((time.perf_counter() - start) * 1000, 2),
            )


def build_default_registry(toolkit: DatabaseToolkit) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="list_tables",
            description="List all tables available in the SQLite analytics database.",
            args_schema=EmptyArgs,
            handler=toolkit.list_tables,
        )
    )
    registry.register(
        RegisteredTool(
            name="describe_table",
            description="Get column names, data types, keys, and 3 sample rows for a table.",
            args_schema=DescribeTableArgs,
            handler=toolkit.describe_table,
        )
    )
    registry.register(
        RegisteredTool(
            name="get_distinct_values",
            description="Get top distinct values and row counts for a specific column (use this to check exact status/enum/region codes before filtering).",
            args_schema=DistinctValuesArgs,
            handler=toolkit.get_distinct_values,
        )
    )
    registry.register(
        RegisteredTool(
            name="execute_read_only_sql",
            description="Execute a single read-only SQLite SELECT query and return rows as JSON.",
            args_schema=RunSQLArgs,
            handler=toolkit.execute_read_only_sql,
        )
    )
    return registry