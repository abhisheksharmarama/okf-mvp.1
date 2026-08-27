"""Builds a DuckDB database from an approved OKF.

The demo runs on generated data. Row counts follow business_role, foreign keys
are drawn from the parent's real key values (so JOINs actually return rows), and
everything is seeded so a demo is reproducible.
"""

import random
from datetime import datetime, timedelta
import duckdb

SEED = 42
ROWS = {"fact": 800, "bridge": 400, "dimension": 40, "lookup": 12, "unknown": 200}

VOCAB = {
    "region": ["North", "South", "East", "West", "Central"],
    "category": ["Electronics", "Apparel", "Home", "Grocery", "Beauty", "Sports"],
    "status": ["Open", "In Progress", "Won", "Lost", "On Hold"],
    "channel": ["Organic", "Paid Search", "Email", "Referral", "Social"],
    "ship": ["Standard Class", "Second Class", "First Class", "Same Day"],
    "city": ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Pune", "Hyderabad"],
    "country": ["India", "United States", "Germany", "Singapore", "Brazil"],
    "product": ["Router X1", "Desk Lamp", "Yoga Mat", "Coffee Beans",
                "Noise Buds", "Steel Bottle", "Trail Shoes", "Wall Clock"],
    "first": ["Aarav", "Priya", "Rohan", "Sara", "Vikram", "Meera",
              "Devika", "Arjun", "Neha", "Kabir"],
    "last": ["Sharma", "Patel", "Nair", "Iyer", "Khan", "Reddy",
             "Bose", "Mehta", "Singh", "Gupta"],
}
DAYS_BACK = 180


def _hint(name: str, *keys) -> bool:
    n = name.lower()
    return any(k in n for k in keys)


def _person(rng):
    return f"{rng.choice(VOCAB['first'])} {rng.choice(VOCAB['last'])}"


def _scalar(col, rng, i):
    n, t, role = col["name"], col["type"], col["semantic_role"]

    if role in ("identifier", "foreign_key") or _hint(n, "_id", "id_", "code", "number"):
        if t == "integer":
            return i + 1000
        return f"{n.split('_')[0][:3].upper()}-{i + 1000}"

    if t == "timestamp" or t == "date" or role == "timestamp":
        d = datetime(2026, 8, 27) - timedelta(
            days=rng.randint(0, DAYS_BACK), hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59))
        return d.date() if t == "date" else d

    if t == "boolean" or role == "flag":
        return rng.random() < 0.3

    if _hint(n, "email"):
        p = _person(rng).lower().replace(" ", ".")
        return f"{p}@example.com"
    if _hint(n, "name", "owner", "customer", "lead", "contact", "student"):
        if _hint(n, "product", "item"):
            return rng.choice(VOCAB["product"])
        return _person(rng)
    if _hint(n, "phone", "mobile"):
        return f"+91-9{rng.randint(100000000, 999999999)}"

    for key in ("region", "category", "status", "channel", "city", "country"):
        if _hint(n, key):
            return rng.choice(VOCAB[key])
    if _hint(n, "ship", "mode", "fulfil"):
        return rng.choice(VOCAB["ship"])
    if _hint(n, "product", "item", "sku"):
        return rng.choice(VOCAB["product"])

    if t in ("numeric", "integer") or role == "measure":
        if _hint(n, "qty", "quantity", "units", "count"):
            return rng.randint(1, 12)
        if _hint(n, "discount", "pct", "percent", "rate"):
            return round(rng.uniform(0, 35), 1)
        if _hint(n, "price", "unit"):
            return round(rng.uniform(99, 4999), 2)
        if _hint(n, "revenue", "amount", "total", "sales", "value"):
            return round(rng.uniform(500, 90000), 2)
        if _hint(n, "profit", "margin"):
            return round(rng.uniform(-2000, 25000), 2)
        if _hint(n, "cost"):
            return round(rng.uniform(300, 60000), 2)
        return rng.randint(1, 500) if t == "integer" else round(rng.uniform(1, 5000), 2)

    return f"{n}_{rng.randint(1, 40)}"


def _order_tables(okf):
    """Parents before children so foreign keys can be sampled from real values."""
    names = [t["name"] for t in okf["tables"]]
    deps = {n: set() for n in names}
    for r in okf.get("relationships", []):
        child = r["from"].rpartition(".")[0]
        parent = r["to"].rpartition(".")[0]
        if child in deps and parent in names and child != parent:
            deps[child].add(parent)
    out, guard = [], 0
    while len(out) < len(names) and guard < len(names) + 2:
        for n in names:
            if n not in out and deps[n].issubset(set(out)):
                out.append(n)
        guard += 1
    return out + [n for n in names if n not in out]


def build_database(okf: dict, con=None):
    """Returns (duckdb connection, {table: rowcount})."""
    con = con or duckdb.connect(":memory:")
    rng = random.Random(SEED)
    by_name = {t["name"]: t for t in okf["tables"]}

    fk_map = {}  # (table, column) -> (parent_table, parent_column)
    for r in okf.get("relationships", []):
        ct, _, cc = r["from"].rpartition(".")
        pt, _, pc = r["to"].rpartition(".")
        fk_map[(ct, cc)] = (pt, pc)

    pools, counts = {}, {}
    for tname in _order_tables(okf):
        t = by_name[tname]
        n_rows = ROWS.get(t.get("business_role", "unknown"), 200)
        cols_sql = []
        data = {}
        for c in t["columns"]:
            key = (tname, c["name"])
            if key in fk_map and fk_map[key] in pools and pools[fk_map[key]]:
                parent_vals = pools[fk_map[key]]
                data[c["name"]] = [rng.choice(parent_vals) for _ in range(n_rows)]
            else:
                data[c["name"]] = [_scalar(c, rng, i) for i in range(n_rows)]
            pools[key] = list(dict.fromkeys(data[c["name"]]))[:200]

            dtype = {"varchar": "VARCHAR", "integer": "BIGINT", "numeric": "DOUBLE",
                     "timestamp": "TIMESTAMP", "date": "DATE",
                     "boolean": "BOOLEAN"}[c["type"]]
            cols_sql.append(f'"{c["name"]}" {dtype}')

        con.execute(f'DROP TABLE IF EXISTS "{tname}"')
        con.execute(f'CREATE TABLE "{tname}" ({", ".join(cols_sql)})')
        rows = list(zip(*[data[c["name"]] for c in t["columns"]]))
        placeholders = ", ".join(["?"] * len(t["columns"]))
        con.executemany(f'INSERT INTO "{tname}" VALUES ({placeholders})', rows)
        counts[tname] = n_rows

    return con, counts
