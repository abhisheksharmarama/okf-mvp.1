# OKF v0.2 — Open Knowledge Format

The OKF is the contract between a client's database and any question asked of it.
It is the only thing the SQL generator is allowed to read. Everything else in this
repository — the UI, the database, the model provider — is replaceable. This is not.

## Design rules

1. **Stored as YAML, shown as anything.** YAML parses reliably, diffs cleanly, and
   can be edited field by field. Free-form markdown does none of those.
2. **Every object carries its own accountability.** `confidence`, `status` and
   `provenance` appear on every table, column and relationship. Without them the OKF
   is a black box, and "the AI drafts, your analyst approves" is an unbacked claim.
3. **Two names, always.** `name` is normalised and safe to put in SQL.
   `physical_name` is the exact string in the source system. A warehouse column
   called `Discount %` cannot appear unquoted in a query; a column called
   `discount_pct` can.
4. **`grain` is mandatory.** A wrong grain produces a confidently wrong number —
   the single failure that ends a pilot. If the model cannot determine it, it must
   say `UNKNOWN` rather than guess.
5. **The LLM never defines structure.** It supplies meaning; the parser supplies
   structure. Anything it returns that does not resolve to a real column is dropped.

## Schema

```yaml
okf_version: "0.2"

source:
  dialect: duckdb              # duckdb | postgres | snowflake | bigquery | redshift
  ingested_at: "2026-08-27T09:00:00Z"
  method: pasted_ddl           # pasted_ddl | uploaded_file | information_schema

tables:
  - name: t_08_e_commerce_orders     # normalised, SQL-safe, unique
    physical_name: "08 E-Commerce Orders"
    description: "Order line items for the storefront."
    grain: "one row per product line item per order"
    business_role: fact               # fact | dimension | bridge | lookup | unknown
    confidence: 0.80                  # 0.0-1.0
    status: draft                     # draft | approved | edited | rejected
    provenance: ai_draft              # ai_draft | human_edited | human_authored
    primary_key: [order_id]
    columns:
      - name: discount_pct
        physical_name: "Discount %"
        type: integer                 # varchar|integer|numeric|timestamp|date|boolean
        description: "Percentage discount applied to this line."
        semantic_role: measure        # identifier | foreign_key | measure |
                                      # dimension | timestamp | flag | unknown
        confidence: 0.45
        status: draft
        provenance: ai_draft
        pii: false

relationships:
  - from: t_08_e_commerce_orders.customer_id
    to: crm_customers.customer_id
    cardinality: many_to_one          # many_to_one | one_to_many | one_to_one
    confidence: 0.78
    status: draft
    provenance: ai_draft

metrics:                              # human-authored only. Never AI-drafted.
  - name: net_revenue
    table: t_08_e_commerce_orders
    expression: 'SUM("total_revenue")'
    description: "Revenue after line-level discount, excluding cancelled orders."
    status: approved
    provenance: human_authored

business_rules: []                    # e.g. converted_lead = status = 'won'
