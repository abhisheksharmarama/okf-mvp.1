okf_version: "0.1"
source:
  dialect: postgres          # postgres | snowflake | bigquery | duckdb
  ingested_at: "2026-08-27T00:00:00Z"
  method: pasted_ddl         # pasted_ddl | information_schema | query_log

tables:
  - name: KNA1
    description: "Customer master record — one row per customer account."
    grain: "one row per kunnr (customer number)"
    business_role: dimension     # fact | dimension | bridge | lookup | unknown
    confidence: 0.62             # 0.0-1.0, model's own estimate
    status: draft                # draft | approved | edited | rejected
    provenance: ai_draft         # ai_draft | human_edited | human_authored
    columns:
      - name: KUNNR
        type: varchar
        description: "Customer number, primary key."
        semantic_role: identifier   # identifier | foreign_key | measure |
                                    # dimension | timestamp | flag | unknown
        confidence: 0.91

relationships:
  - from: VBAK.KUNNR
    to: KNA1.KUNNR
    cardinality: many_to_one
    confidence: 0.74
    status: draft

metrics: []        # stays empty in Phase 2 — humans author these in Phase 3
business_rules: [] # e.g. "converted lead = status='won'"
