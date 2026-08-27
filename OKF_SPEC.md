okf_version: "0.1"
source:
  dialect: postgres
  ingested_at: "2026-08-27T00:00:00Z"
  method: pasted_ddl
tables:
  - name: KNA1
    description: "Customer master record — one row per customer account."
    grain: "one row per kunnr (customer number)"
    business_role: dimension
    confidence: 0.62
    status: draft
    provenance: ai_draft
    primary_key: 
      - KUNNR
    columns:
      - name: KUNNR
        type: varchar
        description: "Customer number, primary key."
        semantic_role: identifier
        confidence: 0.91
        pii: false
relationships:
  - from: VBAK.KUNNR
    to: KNA1.KUNNR
    cardinality: many_to_one
    confidence: 0.74
    status: draft
