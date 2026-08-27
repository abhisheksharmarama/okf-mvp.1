"""Deterministic input handling. No LLM in this file — everything here is testable."""

import re
import pandas as pd

RESERVED = {
    "select", "from", "where", "group", "order", "table", "column", "index",
    "join", "left", "right", "inner", "outer", "on", "as", "by", "case",
    "when", "then", "else", "end", "all", "and", "or", "not", "null",
    "primary", "key", "foreign", "references", "default", "check", "unique",
    "create", "drop", "insert", "update", "delete", "values", "into", "set",
    "union", "having", "limit", "offset", "distinct", "asc", "desc", "in",
    "like", "between", "exists", "is", "add", "alter", "with", "using",
}

TYPE_MAP = {
    "int": "integer", "integer": "integer", "bigint": "integer",
    "smallint": "integer", "tinyint": "integer", "serial": "integer",
    "bigserial": "integer",
    "decimal": "numeric", "numeric": "numeric", "float": "numeric",
    "double": "numeric", "real": "numeric", "money": "numeric",
    "number": "numeric",
    "varchar": "varchar", "char": "varchar", "text": "varchar",
    "string": "varchar", "nvarchar": "varchar", "clob": "varchar",
    "uuid": "varchar", "json": "varchar",
    "timestamp": "timestamp", "datetime": "timestamp",
    "timestamptz": "timestamp",
    "date": "date",
    "bool": "boolean", "boolean": "boolean", "bit": "boolean",
}


def normalise_identifier(raw: str, prefix: str = "c") -> str:
    """'08 E-Commerce Orders' -> 't_08_e_commerce_orders'. Always SQL-safe."""
    s = str(raw).strip().lower()
    s = s.replace("%", "_pct").replace("#", "_num").replace("&", "_and_")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = f"{prefix}_unnamed"
    if s[0].isdigit() or s in RESERVED:
        s = f"{prefix}_{s}"
    return s[:63]


def _dedupe(names):
    """Guarantee uniqueness after normalisation collisions."""
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def normalise_type(raw: str) -> str:
    base = re.split(r"[(\s]", str(raw).strip().lower())[0]
    return TYPE_MAP.get(base, "varchar")


def _build(tables_raw: dict) -> dict:
    """tables_raw: {physical_table: [(physical_col, raw_type), ...]} -> schema dict."""
    out = {"tables": []}
    tnames = _dedupe([normalise_identifier(t, "t") for t in tables_raw])
    for tname, (phys_t, cols) in zip(tnames, tables_raw.items()):
        cnames = _dedupe([normalise_identifier(c, "c") for c, _ in cols])
        out["tables"].append({
            "name": tname,
            "physical_name": str(phys_t),
            "columns": [
                {"name": cn, "physical_name": str(pc), "type": normalise_type(ty)}
                for cn, (pc, ty) in zip(cnames, cols)
            ],
        })
    return out


def parse_text_input(text: str) -> dict:
    """Accepts CREATE TABLE DDL, or CSV lines of `table,column,type`."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return {"tables": []}

    if re.search(r"create\s+table", text, re.IGNORECASE):
        tables_raw = {}
        pattern = re.compile(
            r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"`\[]?([\w. ]+?)[\"`\]]?\s*\((.*?)\)\s*;",
            re.IGNORECASE | re.DOTALL,
        )
        for m in pattern.finditer(text):
            tname = m.group(1).strip().split(".")[-1]
            cols, depth, buf = [], 0, ""
            for ch in m.group(2):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    cols.append(buf); buf = ""
                else:
                    buf += ch
            cols.append(buf)
            parsed = []
            for c in cols:
                c = c.strip()
                if not c:
                    continue
                first = c.split()[0].lower().strip('"`[]')
                if first in ("primary", "foreign", "unique", "constraint",
                             "check", "key", "index"):
                    continue
                parts = c.replace('"', " ").replace("`", " ").split()
                if len(parts) >= 2:
                    parsed.append((parts[0], parts[1]))
            if parsed:
                tables_raw[tname] = parsed
        return _build(tables_raw)

    # CSV mode: table,column,type
    tables_raw = {}
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0] and parts[1]:
            if parts[0].lower() == "table" and parts[1].lower() == "column":
                continue  # header row
            tables_raw.setdefault(parts[0], []).append((parts[1], parts[2]))
    return _build(tables_raw)


def parse_uploaded_file(uploaded_file) -> dict:
    """DATA SHIELD: reads 20 rows only to infer types, then discards every row."""
    name = uploaded_file.name.rsplit(".", 1)[0]
    if uploaded_file.name.lower().endswith(".csv"):
        df = pd.read_csv(uploaded_file, nrows=20)
    else:
        df = pd.read_excel(uploaded_file, nrows=20)

    cols = []
    for col, dtype in df.dtypes.items():
        if pd.api.types.is_bool_dtype(dtype):
            t = "boolean"
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            t = "timestamp"
        elif pd.api.types.is_float_dtype(dtype):
            t = "numeric"
        elif pd.api.types.is_integer_dtype(dtype):
            t = "integer"
        else:
            t = "varchar"
        cols.append((col, t))

    del df  # rows gone before anything leaves this function
    return _build({name: cols})
