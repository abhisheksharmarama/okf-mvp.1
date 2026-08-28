"""What can be asked is decided by the approved OKF, not by a model.

Suggestions are generated from semantic_role plus real cardinality read from the
demo database. No LLM call, no latency, and — the point — it is impossible to
suggest a question the semantic layer cannot answer.
"""

import sqlglot
from sqlglot import exp

MAX_CARDINALITY = 25
MIN_TOPN_CARDINALITY = 7

# Dimensions a business person actually asks about first. Deterministic, and it
# keeps the demo's opening question from being an arbitrary low-cardinality column.
PREFERRED_DIMS = ("region", "country", "category", "segment", "channel",
                  "status", "type", "product", "city")


def _label(name: str) -> str:
    return name.replace("_", " ").strip()


def _bare(measure: str) -> str:
    """'total_revenue' -> 'revenue', for phrasings that already carry a verb."""
    lab = _label(measure)
    head = lab.split(" ")[0].lower()
    if head in ("total", "net", "gross", "average", "avg", "sum") and " " in lab:
        return lab.split(" ", 1)[1]
    return lab


def _measure_phrase(measure: str) -> str:
    """'total_revenue' -> 'Total revenue', not 'Total total revenue'."""
    lab = _label(measure)
    if lab.split(" ")[0].lower() in ("total", "net", "gross", "average", "avg", "sum"):
        return lab[0].upper() + lab[1:]
    return "Total " + lab


def _approved(obj) -> bool:
    return obj.get("status") in ("approved", "edited")


def _inventory(okf, only_approved=True):
    """Returns measures, dimensions, timestamps as (table, column) pairs."""
    m, d, ts = [], [], []
    for t in okf["tables"]:
        if only_approved and not _approved(t):
            continue
        for c in t["columns"]:
            if only_approved and not _approved(c):
                continue
            if c["semantic_role"] == "measure":
                m.append((t["name"], c["name"]))
            elif c["semantic_role"] == "dimension":
                d.append((t["name"], c["name"]))
            elif c["semantic_role"] == "timestamp":
                ts.append((t["name"], c["name"]))
    if only_approved and not m and not d:
        return _inventory(okf, only_approved=False)
    return m, d, ts


def _cardinality(con, table, column):
    try:
        return con.execute(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"').fetchone()[0]
    except Exception:
        return None


def _rank_dimensions(con, dims):
    """Chartable first, business-recognisable first, then by cardinality."""
    scored = []
    for t, c in dims:
        n = _cardinality(con, t, c) if con else None
        chartable = 0 if (n is None or 1 < n <= MAX_CARDINALITY) else 1
        preferred = 0 if any(p in c for p in PREFERRED_DIMS) else 1
        scored.append((chartable, preferred, n if n is not None else 99, t, c))
    scored.sort()
    return [(t, c, n) for _, _, n, t, c in scored]


def _headline_measure(okf, measures):
    """A certified metric wins. Otherwise prefer a revenue-like measure."""
    for m in okf.get("metrics", []):
        if _approved(m):
            return m["table"], m["name"], True
    for t, c in measures:
        if any(k in c for k in ("revenue", "amount", "sales", "total", "value")):
            return t, c, False
    return (measures[0][0], measures[0][1], False) if measures else (None, None, False)


def _joinable(okf, table_a, table_b):
    for r in okf.get("relationships", []):
        if not _approved(r):
            continue
        ta = r["from"].rpartition(".")[0]
        tb = r["to"].rpartition(".")[0]
        if {ta, tb} == {table_a, table_b}:
            return True
    return False


def initial_suggestions(okf, con=None, n=4):
    measures, dims, stamps = _inventory(okf)
    if not measures and not dims:
        return []
    mt, mc, certified = _headline_measure(okf, measures)
    dims = _rank_dimensions(con, dims)
    out = []

    phrase = _measure_phrase(mc) if mc else ""
    same = [(t, c, k) for t, c, k in dims if t == mt]
    other = [(t, c, k) for t, c, k in dims if t != mt and _joinable(okf, mt, t)]

    if mc and same:
        out.append(f"{phrase} by {_label(same[0][1])}")
    if mc and other:
        # The cross-table question. This is the one that proves the join.
        out.append(f"{phrase} by {_label(other[0][1])}")
    if mc and stamps:
        out.append(f"Monthly {_bare(mc)} trend")
    # Top-N only makes sense when there is something to rank.
    wide = [d for d in same if d[2] and d[2] >= MIN_TOPN_CARDINALITY]
    if mc and wide:
        out.append(f"Top 5 {_label(wide[0][1])} by {_bare(mc)}")
    if len(out) < n and len(same) > 1:
        out.append(f"{phrase} by {_label(same[1][1])}")
    if len(out) < n and same:
        out.append(f"How many records by {_label(same[0][1])}")

    seen, uniq = set(), []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return uniq[:n]


def _columns_used(sql):
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return set()
    return {c.name for c in tree.find_all(exp.Column)}


def followups(okf, sql, con=None, n=3):
    """Next questions, derived from what the last query did NOT use."""
    measures, dims, stamps = _inventory(okf)
    if not measures:
        return []
    mt, mc, _ = _headline_measure(okf, measures)
    used = _columns_used(sql)
    dims = _rank_dimensions(con, dims)
    out = []

    phrase = _measure_phrase(mc)
    unused = [(t, c, k) for t, c, k in dims if c not in used]
    for t, c, k in unused[:2]:
        if t == mt or _joinable(okf, mt, t):
            out.append(f"{phrase} by {_label(c)}")

    if stamps and not any(s[1] in used for s in stamps):
        out.append(f"Monthly {_bare(mc)} trend")

    used_dims = [(t, c, k) for t, c, k in dims if c in used]
    if used_dims and used_dims[0][2] and used_dims[0][2] >= MIN_TOPN_CARDINALITY:
        out.append(f"Top 5 {_label(used_dims[0][1])} by {_bare(mc)}")

    other_measures = [(t, c) for t, c in measures if c not in used and c != mc]
    if other_measures and used_dims:
        out.append(f"{_measure_phrase(other_measures[0][1])} by {_label(used_dims[0][1])}")

    seen, uniq = set(), []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return uniq[:n]


def suggest_chart(df, okf=None):
    """Bar, line or table — decided by data shape, overridable by the user."""
    import pandas as pd
    if df is None or df.empty or df.shape[1] < 2:
        return "Table"
    x, y = df.columns[0], df.columns[1]
    if not pd.api.types.is_numeric_dtype(df[y]):
        return "Table"
    if pd.api.types.is_datetime64_any_dtype(df[x]):
        return "Line"
    if df[x].nunique() <= MAX_CARDINALITY:
        return "Bar"
    return "Table"
