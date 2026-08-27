"""The Critic. Deterministic, no LLM. Nothing executes until this passes."""

import re
import sqlglot
from sqlglot import exp

FORBIDDEN = (
    exp.Drop, exp.Delete, exp.Insert, exp.Update, exp.Create, exp.Alter,
    exp.Grant, exp.TruncateTable, exp.Merge,
)
MAX_ROWS = 5000


def _allowlist(okf):
    tables, cols = set(), {}
    for t in okf.get("tables", []):
        tables.add(t["name"])
        cols[t["name"]] = {c["name"] for c in t["columns"]}
    return tables, cols


def strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:sql)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"```\s*$", "", t)
    return t.strip().rstrip(";").strip()


def check_sql(sql: str, okf: dict):
    """Returns (ok: bool, errors: [str], safe_sql: str)."""
    errors = []
    sql = strip_fences(sql)
    if not sql:
        return False, ["Model returned no SQL."], ""

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as ex:
        return False, [f"SQL did not parse: {ex}"], sql

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return False, [f"Expected exactly 1 statement, found {len(statements)}."], sql

    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)) and not tree.find(exp.Select):
        errors.append("Only SELECT statements are allowed.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            errors.append(f"Forbidden operation: {type(node).__name__.upper()}")
        if isinstance(node, exp.Command):
            errors.append("Raw command statements are not allowed.")

    allowed_tables, allowed_cols = _allowlist(okf)
    cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    used_tables = set()
    for tnode in tree.find_all(exp.Table):
        name = tnode.name
        used_tables.add(name)
        if name not in allowed_tables and name not in cte_names:
            errors.append(f"Table '{name}' is not in the approved OKF.")

    known = set()
    for t in used_tables & allowed_tables:
        known |= allowed_cols[t]
    aliases = {a.alias_or_name for a in tree.find_all(exp.Alias)}
    # With CTEs, intermediate columns legitimately aren't in the OKF.
    # Table allowlisting still holds; EXPLAIN catches bad column references.
    if cte_names:
        known = set()
    if known:
        for cnode in tree.find_all(exp.Column):
            cn = cnode.name
            if cn == "*" or cn in known or cn in aliases:
                continue
            errors.append(f"Column '{cn}' is not in the approved OKF.")

    for j in tree.find_all(exp.Join):
        if not j.args.get("on") and not j.args.get("using"):
            kind = (j.args.get("kind") or "").upper()
            if kind != "CROSS":
                errors.append("JOIN without an ON condition (Cartesian product blocked).")
            else:
                errors.append("CROSS JOIN is blocked.")

    if errors:
        return False, sorted(set(errors)), sql

    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(MAX_ROWS)
    return True, [], tree.sql(dialect="duckdb")


def explain_ok(con, sql: str):
    """Runs EXPLAIN. Catches invalid references the parser can't see."""
    try:
        con.execute(f"EXPLAIN {sql}")
        return True, ""
    except Exception as ex:
        return False, str(ex).split("\n")[0]
