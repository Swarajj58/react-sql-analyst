import sqlglot
from sqlglot import exp


class SQLSecurityError(Exception):
    """Raised when a SQL query violates read-only or single-statement policy."""


# Any AST node matching these types is strictly forbidden anywhere in the query tree
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,   # Catches PRAGMA, ATTACH, VACUUM, REINDEX, etc.
    exp.Pragma,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)


def validate_and_rewrite_sql(raw_sql: str, max_rows: int = 50) -> str:
    """
    Parses SQL into an AST, enforces single read-only SELECT policy,
    and caps result size via LIMIT injection.
    """
    cleaned = raw_sql.strip()
    if not cleaned:
        raise SQLSecurityError("Empty SQL query provided.")

    try:
        statements = sqlglot.parse(cleaned, read="sqlite")
    except sqlglot.errors.ParseError as exc:
        raise SQLSecurityError(f"SQL syntax parse error: {exc}") from exc

    # Filter out trailing empty statements from a single trailing semicolon
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLSecurityError(
            f"Multi-statement SQL is forbidden (found {len(statements)} statements)."
        )

    tree = statements[0]

    # Root must be a SELECT or set operation (UNION/INTERSECT/EXCEPT)
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise SQLSecurityError(
            f"Only SELECT queries are permitted. Got root operation: {tree.key.upper()}"
        )

    # Walk entire AST (including CTEs and subqueries) to block hidden mutations
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLSecurityError(
                f"Forbidden SQL operation detected in query tree: {node.key.upper()}"
            )

    # Enforce row limit if not already specified (or clamp if larger than max_rows)
    if isinstance(tree, exp.Select):
        existing_limit = tree.args.get("limit")
        if existing_limit is None:
            tree = tree.limit(max_rows)
        else:
            try:
                limit_val = int(existing_limit.expression.name)
                if limit_val > max_rows:
                    tree = tree.limit(max_rows)
            except (AttributeError, ValueError):
                tree = tree.limit(max_rows)

    return tree.sql(dialect="sqlite")