"""The only file that talks to a model. Everything else is deterministic."""

import os
import json
import okf_spec as S

NO_ANSWER = "INSUFFICIENT_CONTEXT"


def _secret(key, default=None):
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def call_llm(prompt: str, json_mode: bool = False) -> str:
    provider = _secret("LLM_PROVIDER", "gemini")
    if provider == "claude":
        from anthropic import Anthropic
        client = Anthropic(api_key=_secret("ANTHROPIC_API_KEY"))
        sys = "Output valid JSON only. No prose, no markdown fences." if json_mode else ""
        r = client.messages.create(
            model=_secret("CLAUDE_MODEL", "claude-sonnet-4-5"),
            max_tokens=8000, temperature=0, system=sys,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")

    import google.generativeai as genai
    genai.configure(api_key=_secret("GEMINI_API_KEY"))
    cfg = {"temperature": 0.0, "max_output_tokens": 8000}
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    model = genai.GenerativeModel(_secret("GEMINI_MODEL", "gemini-2.0-flash"),
                                  generation_config=cfg)
    return model.generate_content(prompt).text


# ---------------------------------------------------------------- OKF drafting

def build_okf_prompt(schema: dict) -> str:
    compact = {
        t["name"]: {
            "original_table_name": t["physical_name"],
            "columns": [{"name": c["name"], "type": c["type"],
                         "original_name": c["physical_name"]} for c in t["columns"]],
        }
        for t in schema["tables"]
    }
    return f"""You are a senior data architect reviewing an unfamiliar database.

You are given ONLY table names, column names and column types. No row data.
Infer meaning. You will be wrong sometimes — that is expected and acceptable.
An honest low score is more valuable to us than a confident guess.

Return JSON with this exact shape and nothing else:

{{
  "tables": [
    {{
      "name": "<echo the table name exactly as given>",
      "description": "<one sentence: what this table holds>",
      "grain": "<what exactly does ONE ROW represent>",
      "business_role": "one of {S.TABLE_ROLES}",
      "confidence": 0.0,
      "primary_key": ["<column names>"],
      "columns": [
        {{
          "name": "<echo the column name exactly as given>",
          "description": "<one sentence>",
          "semantic_role": "one of {S.SEMANTIC_ROLES}",
          "confidence": 0.0,
          "pii": true
        }}
      ]
    }}
  ],
  "relationships": [
    {{"from": "table.column", "to": "table.column",
      "cardinality": "one of {S.CARDINALITIES}", "confidence": 0.0}}
  ]
}}

Scoring rules — read carefully:
- confidence = your honest probability that the data owner would accept this
  description with NO edits.
- Score LOW (below 0.4) when the MEANING is unclear: opaque names (data,
  lookup_1, flag_2), numeric codes that map to an external code table, or any
  column whose definition is a business policy rather than a fact.
- Do NOT score low merely because you don't know a column's list of values.
  Knowing that "region" holds regions is enough — that is a HIGH score.
- 'grain' is the single most important field. If you cannot tell whether a row
  is one-per-entity or one-per-event, say so explicitly and score the table
  below 0.5. A wrong grain produces confidently wrong numbers.
- semantic_role drives SQL generation: 'measure' is something you would SUM or
  AVG; 'dimension' is something you would GROUP BY; 'identifier' is the key of
  this table; 'foreign_key' points at another table.
- Propose a relationship ONLY where names and types genuinely align. An invented
  join is worse than a missing one.

SCHEMA:
{json.dumps(compact, indent=2)}"""


# ------------------------------------------------------------- SQL generation

def okf_context(okf: dict) -> str:
    lines = []
    for t in okf["tables"]:
        flag = "" if t["status"] in ("approved", "edited") else "  [UNAPPROVED DRAFT]"
        lines.append(f'TABLE "{t["name"]}"{flag} — {t["description"]}')
        lines.append(f'  grain: {t["grain"]}')
        for c in t["columns"]:
            lines.append(f'  - "{c["name"]}" ({c["type"]}, {c["semantic_role"]}) — {c["description"]}')
    joins = [r for r in okf.get("relationships", [])
             if r.get("status") in ("approved", "edited")]
    if joins:
        lines.append("APPROVED JOINS (use only these):")
        for r in joins:
            lines.append(f'  {r["from"]} = {r["to"]}  ({r["cardinality"]})')
    else:
        lines.append("APPROVED JOINS: none. Do not join tables.")
    if okf.get("metrics"):
        lines.append("CERTIFIED METRICS (use the expression verbatim):")
        for m in okf["metrics"]:
            lines.append(f'  {m["name"]} = {m["expression"]}  (on {m["table"]}) — {m["description"]}')
    if okf.get("business_rules"):
        lines.append("BUSINESS RULES:")
        for b in okf["business_rules"]:
            lines.append(f'  {b["name"]}: {b["definition"]}')
    return "\n".join(lines)


def build_sql_prompt(okf: dict, question: str) -> str:
    return f"""You write DuckDB SQL. You may use ONLY the tables, columns, joins
and metrics listed below. They are a human-approved semantic layer.

{okf_context(okf)}

RULES:
- Output raw SQL only. No explanation, no markdown fences.
- A single SELECT statement. Never DROP, DELETE, INSERT, UPDATE or CREATE.
- Quote every identifier with double quotes.
- Join only on the approved joins listed above. Never invent a join.
- If a certified metric answers the question, use its expression verbatim.
- Aggregate. Do not return raw rows unless the question explicitly asks for a list.
- Always add a LIMIT.
- If the question cannot be answered from the columns above, output exactly:
  {NO_ANSWER}: <short reason naming what is missing>

QUESTION: {question}"""


def build_repair_prompt(okf: dict, question: str, bad_sql: str, errors: list) -> str:
    return f"""{build_sql_prompt(okf, question)}

Your previous attempt was REJECTED by the safety validator.

Rejected SQL:
{bad_sql}

Reasons:
{chr(10).join('- ' + e for e in errors)}

Output corrected raw SQL only."""


def build_explain_prompt(question, sql, rows_preview):
    return f"""Answer the user's question in at most 3 short sentences, using only
the query result below. State the number plainly. No preamble, no caveats about
being an AI. If the result is empty, say the query returned no matching rows.

QUESTION: {question}
SQL: {sql}
RESULT: {rows_preview}"""
