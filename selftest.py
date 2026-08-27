"""Runs inside the deployed app. No LLM, no network — pure logic checks.

Every rule this project depends on is asserted here. If this page is green,
the deterministic half of the product works. The LLM half is judged by eye.
"""

import json
import parsers
import validator
import mockdata
import safety
import llm
import okf_spec as S

SCHEMA_TEXT = (
    "08 E-Commerce Orders,Order ID,varchar\n"
    "08 E-Commerce Orders,Order Date,timestamp\n"
    "08 E-Commerce Orders,Customer ID,varchar\n"
    "08 E-Commerce Orders,Region,varchar\n"
    "08 E-Commerce Orders,Quantity,int\n"
    "08 E-Commerce Orders,Discount %,int\n"
    "08 E-Commerce Orders,Total Revenue,decimal\n"
    "crm_customers,Customer ID,varchar\n"
    "crm_customers,Customer Name,varchar\n"
    "crm_customers,Country,varchar"
)

DDL = """CREATE TABLE KNA1 (
  KUNNR VARCHAR(10) NOT NULL,
  NAME1 VARCHAR(35),
  ERDAT DATE,
  PRIMARY KEY (KUNNR)
);
CREATE TABLE VBAK (
  VBELN VARCHAR(10),
  KUNNR VARCHAR(10),
  NETWR DECIMAL(15,2)
);"""


def _stub(schema):
    out = {"tables": [], "relationships": []}
    roles = {"varchar": "dimension", "integer": "measure", "numeric": "measure",
             "timestamp": "timestamp", "date": "timestamp", "boolean": "flag"}
    for t in schema["tables"]:
        cols = []
        for c in t["columns"]:
            r = "identifier" if c["name"].endswith("_id") else roles[c["type"]]
            if c["name"] == "customer_id" and t["name"].startswith("t_08"):
                r = "foreign_key"
            cols.append({"name": c["name"], "description": f'The {c["name"]}.',
                         "semantic_role": r, "confidence": 0.85,
                         "pii": "name" in c["name"]})
        out["tables"].append({
            "name": t["name"], "description": "T.", "grain": "one row per record",
            "business_role": "fact" if t["name"].startswith("t_08") else "dimension",
            "confidence": 0.8, "primary_key": [t["columns"][0]["name"]], "columns": cols})
    out["relationships"] = [{"from": "t_08_e_commerce_orders.customer_id",
                             "to": "crm_customers.customer_id",
                             "cardinality": "many_to_one", "confidence": 0.78}]
    return out


def run_all():
    r = []

    def ck(group, name, cond, detail=""):
        r.append({"group": group, "name": name, "ok": bool(cond),
                  "detail": "" if cond else str(detail)[:160]})

    # --- parsing -----------------------------------------------------------
    g = "Reading a schema"
    s = parsers.parse_text_input(SCHEMA_TEXT)
    ck(g, "Two tables read from pasted CSV", len(s["tables"]) == 2,
       [t["name"] for t in s["tables"]])
    t0 = s["tables"][0]
    ck(g, "Table starting with a digit becomes SQL-safe",
       t0["name"] == "t_08_e_commerce_orders", t0["name"])
    ck(g, "Original table name is preserved",
       t0["physical_name"] == "08 E-Commerce Orders", t0["physical_name"])
    ck(g, "'Discount %' becomes discount_pct",
       any(c["name"] == "discount_pct" for c in t0["columns"]),
       [c["name"] for c in t0["columns"]])
    ck(g, "SQL keyword used as a name is escaped",
       parsers.normalise_identifier("select") == "c_select")
    ck(g, "Duplicate names after cleaning are separated",
       [c["name"] for c in parsers.parse_text_input(
           "t,Order ID,varchar\nt,order id,varchar")["tables"][0]["columns"]]
       == ["order_id", "order_id_1"])
    d = parsers.parse_text_input(DDL)
    ck(g, "CREATE TABLE statements are read", len(d["tables"]) == 2)
    ck(g, "PRIMARY KEY clause is not mistaken for a column",
       len(d["tables"][0]["columns"]) == 3,
       [c["name"] for c in d["tables"][0]["columns"]])
    ck(g, "DECIMAL is mapped to a number type",
       d["tables"][1]["columns"][2]["type"] == "numeric")

    # --- merge + validator -------------------------------------------------
    g = "Checking the AI draft"
    okf = validator.merge_llm_draft(s, _stub(s), "pasted_ddl")
    ck(g, "A clean draft passes the spec check", validator.validate_okf(okf) == [],
       validator.validate_okf(okf)[:2])
    rev = [c for c in okf["tables"][0]["columns"] if c["name"] == "total_revenue"][0]
    ck(g, "Revenue is forced to low confidence until a human confirms it",
       rev["confidence"] <= 0.40, rev["confidence"])
    ck(g, "Revenue description says the definition is a policy choice",
       "business policy" in rev["description"])

    bad = json.loads(json.dumps(_stub(s)))
    bad["tables"][0].pop("grain")
    bad["tables"][0]["business_role"] = "event_log"
    bad["tables"][0]["columns"][0]["semantic_role"] = "primary_key"
    bad["tables"][0]["columns"][1]["confidence"] = 7
    bad["tables"][0]["columns"][2].pop("pii")
    bad["relationships"].append({"from": "t_08_e_commerce_orders.ghost",
                                 "to": "crm_customers.customer_id",
                                 "cardinality": "many_to_one", "confidence": 0.9})
    o2 = validator.merge_llm_draft(s, bad, "pasted_ddl")
    ck(g, "Missing grain is replaced with an explicit UNKNOWN",
       o2["tables"][0]["grain"].startswith("UNKNOWN"))
    ck(g, "Invented table role falls back to unknown",
       o2["tables"][0]["business_role"] == "unknown")
    ck(g, "Invented column role falls back to unknown",
       o2["tables"][0]["columns"][0]["semantic_role"] == "unknown")
    ck(g, "Impossible confidence value is clamped",
       o2["tables"][0]["columns"][1]["confidence"] <= 1.0)
    ck(g, "Missing personal-data flag defaults to false",
       o2["tables"][0]["columns"][2]["pii"] is False)
    ck(g, "A join to a column that does not exist is discarded",
       len(o2["relationships"]) == 1, o2["relationships"])
    ck(g, "Repaired draft still passes the spec check", validator.validate_okf(o2) == [])

    broken = json.loads(json.dumps(okf))
    broken["tables"][0]["columns"][0].pop("semantic_role")
    broken.pop("okf_version")
    e = validator.validate_okf(broken)
    ck(g, "Spec check catches a missing column role", any("semantic_role" in x for x in e))
    ck(g, "Spec check catches a missing version stamp", any("okf_version" in x for x in e))

    ck(g, "Review queue puts the least certain item first",
       validator.review_queue(okf)[0]["conf"] <= validator.review_queue(okf)[-1]["conf"])
    ck(g, "Approval starts at zero percent", validator.approval_stats(okf)["pct"] == 0)

    # --- data --------------------------------------------------------------
    g = "Building the demo dataset"
    for t in okf["tables"]:
        for o in [t] + t["columns"]:
            o["status"] = "approved"
    con, counts = mockdata.build_database(okf)
    ck(g, "Order table has 800 rows", counts["t_08_e_commerce_orders"] == 800, counts)
    ck(g, "Customer table has 40 rows", counts["crm_customers"] == 40, counts)
    joined = con.execute(
        'SELECT COUNT(*) FROM "t_08_e_commerce_orders" o '
        'JOIN "crm_customers" c ON o."customer_id" = c."customer_id"').fetchone()[0]
    ck(g, "Joining the two tables returns every order", joined == 800, joined)
    ck(g, "Region contains real region names",
       con.execute('SELECT COUNT(DISTINCT "region") FROM "t_08_e_commerce_orders"'
                   ).fetchone()[0] == 5)
    ck(g, "Revenue values are plausible amounts",
       500 < con.execute('SELECT MAX("total_revenue") FROM "t_08_e_commerce_orders"'
                         ).fetchone()[0] < 100000)
    con2, _ = mockdata.build_database(okf)
    ck(g, "The same dataset is generated every time",
       round(con.execute('SELECT SUM("total_revenue") FROM "t_08_e_commerce_orders"'
                         ).fetchone()[0], 2)
       == round(con2.execute('SELECT SUM("total_revenue") FROM "t_08_e_commerce_orders"'
                             ).fetchone()[0], 2))

    # --- safety ------------------------------------------------------------
    g = "Blocking unsafe SQL"
    allow = [
        'SELECT "region", SUM("total_revenue") AS r FROM "t_08_e_commerce_orders" GROUP BY "region"',
        'SELECT c."country", SUM(o."total_revenue") AS r FROM "t_08_e_commerce_orders" o '
        'JOIN "crm_customers" c ON o."customer_id" = c."customer_id" GROUP BY c."country"',
        'WITH p AS (SELECT "region", SUM("total_revenue") AS r FROM "t_08_e_commerce_orders" '
        'GROUP BY "region") SELECT * FROM p ORDER BY r DESC',
        'SELECT COUNT(*) AS n FROM "t_08_e_commerce_orders"',
    ]
    for sql in allow:
        ok, errs, _ = safety.check_sql(sql, okf)
        ck(g, f"Allowed: {sql[:46]}…", ok, errs)

    block = [
        ('DROP TABLE "t_08_e_commerce_orders"', "Forbidden"),
        ('DELETE FROM "t_08_e_commerce_orders"', "Forbidden"),
        ('INSERT INTO "crm_customers" VALUES (1)', "Forbidden"),
        ('UPDATE "crm_customers" SET "country" = \'x\'', "Forbidden"),
        ('SELECT 1; DROP TABLE "crm_customers";', "statement"),
        ('SELECT * FROM "t_08_e_commerce_orders", "crm_customers"', "Cartesian"),
        ('SELECT * FROM "t_08_e_commerce_orders" CROSS JOIN "crm_customers"', "CROSS"),
        ('SELECT * FROM "payroll"', "not in the approved OKF"),
        ('SELECT "salary" FROM "crm_customers"', "not in the approved OKF"),
        ('WITH t AS (SELECT * FROM "payroll") SELECT * FROM t', "not in the approved OKF"),
        ('not sql at all ((', "parse"),
    ]
    for sql, frag in block:
        ok, errs, _ = safety.check_sql(sql, okf)
        ck(g, f"Blocked: {sql[:46]}…",
           (not ok) and any(frag.lower() in x.lower() for x in errs), errs)

    ok, _, safe = safety.check_sql('SELECT "region" FROM "t_08_e_commerce_orders"', okf)
    ck(g, "A row limit is added when the model forgets one", "LIMIT" in safe.upper(), safe)
    ok, _, safe2 = safety.check_sql(
        'SELECT "region" FROM "t_08_e_commerce_orders" LIMIT 10', okf)
    ck(g, "An existing row limit is left alone", safe2.upper().count("LIMIT") == 1, safe2)
    ok, _, safe3 = safety.check_sql(
        '```sql\nSELECT "region" FROM "t_08_e_commerce_orders";\n```', okf)
    ck(g, "Markdown fences around the SQL are removed", ok and safe3.startswith("SELECT"))

    g = "Running a real question"
    sql = ('SELECT "region", SUM("total_revenue") AS revenue '
           'FROM "t_08_e_commerce_orders" GROUP BY "region" ORDER BY revenue DESC')
    ok, errs, safe = safety.check_sql(sql, okf)
    ck(g, "The demo query passes every check", ok, errs)
    eok, emsg = safety.explain_ok(con, safe)
    ck(g, "The database plans the query without error", eok, emsg)
    df = con.execute(safe).df()
    ck(g, "Five regions come back with a number each", df.shape == (5, 2), df.shape)
    okb, _, safeb = safety.check_sql(
        'SELECT "region", SUM("total_revenue") FROM "t_08_e_commerce_orders" '
        'GROUP BY "quantity"', okf)
    ck(g, "A query that parses but cannot run is still caught",
       okb and not safety.explain_ok(con, safeb)[0])

    g = "Prompt wiring"
    p = llm.build_okf_prompt(s)
    ck(g, "Draft prompt sends original column names", "Discount %" in p)
    ck(g, "Draft prompt sends original table names", "08 E-Commerce Orders" in p)
    ck(g, "Draft prompt demands a grain", "grain" in p)
    okf["metrics"] = [{"name": "net_revenue", "table": "t_08_e_commerce_orders",
                       "expression": 'SUM("total_revenue")', "description": "Revenue.",
                       "status": "approved", "provenance": "human_authored"}]
    c = llm.okf_context(okf)
    ck(g, "Question prompt includes certified metrics", "net_revenue" in c)
    ck(g, "Question prompt includes approved joins", "APPROVED JOINS" in c)
    ck(g, "Question prompt defines the refusal answer",
       llm.NO_ANSWER in llm.build_sql_prompt(okf, "headcount?"))

    return r
