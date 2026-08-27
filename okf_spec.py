"""OKF v0.2 — the contract. Every other module reads its rules from here."""

OKF_VERSION = "0.2"

TABLE_ROLES = ["fact", "dimension", "bridge", "lookup", "unknown"]
SEMANTIC_ROLES = [
    "identifier", "foreign_key", "measure", "dimension",
    "timestamp", "flag", "unknown",
]
STATUSES = ["draft", "approved", "edited", "rejected"]
PROVENANCES = ["ai_draft", "human_edited", "human_authored"]
CARDINALITIES = ["many_to_one", "one_to_many", "one_to_one"]
TYPES = ["varchar", "integer", "numeric", "timestamp", "date", "boolean"]

TABLE_REQUIRED = [
    "name", "physical_name", "description", "grain", "business_role",
    "confidence", "status", "provenance", "columns",
]
COLUMN_REQUIRED = [
    "name", "physical_name", "type", "description", "semantic_role",
    "confidence", "status", "provenance", "pii",
]
RELATIONSHIP_REQUIRED = [
    "from", "to", "cardinality", "confidence", "status", "provenance",
]
TOP_LEVEL_REQUIRED = ["okf_version", "source", "tables", "relationships"]

# Columns whose meaning is business policy, never inferable from metadata.
KPI_KEYWORDS = [
    "revenue", "profit", "margin", "cost", "conversion", "churn",
    "retention", "arpu", "ltv", "cac", "gmv", "aov", "ebitda",
]

REVIEW_THRESHOLD = 0.60  # below this, a human must look before the OKF is usable
