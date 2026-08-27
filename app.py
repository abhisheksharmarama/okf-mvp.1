import os
import json
import yaml
import re
import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="OKF MVP", layout="wide")

def parse_input(text: str) -> dict:
    tables = {}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines: return tables
    
    if "," in lines[0] and not lines[0].lower().startswith("create"):
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                t, c, typ = parts[0], parts[1], parts[2]
                if t not in tables: tables[t] = []
                tables[t].append({"name": c, "type": typ})
    else:
        matches = re.finditer(r"CREATE TABLE\s+([a-zA-Z0-9_]+)\s*\((.*?)\);", text, re.IGNORECASE | re.DOTALL)
        for match in matches:
            t = match.group(1)
            tables[t] = []
            for col in match.group(2).split(","):
                parts = col.strip().split()
                if len(parts) >= 2:
                    tables[t].append({"name": parts[0], "type": parts[1]})
    return tables

def generate_okf(schema_dict: dict) -> str:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        "gemini-3.6-flash", 
        generation_config={"response_mime_type": "application/json", "temperature": 0.0}
    )
    
    prompt = f"""
    You are a data architect. Below is a database schema: table names, column names, and column types. Nothing else. No row data.
    Infer what each table and column means. You will be wrong sometimes — that is expected and acceptable. Do NOT guess confidently.

    Rules:
    - If a table's purpose cannot be determined from metadata alone, set business_role: unknown, confidence below 0.3, and say so in the description.
    - confidence is your honest probability that a human data owner would accept this description unchanged.
    - Calibration: across a typical schema, roughly 20% of columns should score below 0.5. If your output has no low-confidence entries, you are not being honest. Any column whose meaning depends on an external code table, lookup, or convention not visible in this metadata MUST score below 0.4.
    - EVERY table MUST include a 'grain' field explaining exactly what a single row represents.
    - EVERY table and column MUST include 'status: draft' and 'provenance: ai_draft'.
    - EVERY column MUST include a 'pii' boolean (true/false).
    - Propose relationships only where column names and types genuinely align. Do not invent joins.
    - Output valid JSON matching the OKF v0.1 schema exactly.

    SCHEMA:
    {json.dumps(schema_dict, indent=2)}
    """
    return model.generate_content(prompt).text

st.title("OKF MVP — Phase 2")
st.markdown("Paste your DDL or CSV (`table,column,type`). **Max 30 tables.**")

schema_input = st.text_area("Schema Input", height=200)

if st.button("Generate OKF") and schema_input:
    parsed_dict = parse_input(schema_input)
    
    if len(parsed_dict) > 30:
        st.error("Too many tables! Please limit to 30.")
    elif not parsed_dict:
        st.error("Could not parse input. Check formatting.")
    else:
        with st.spinner("Drafting OKF via LLM..."):
            try:
                raw_json = generate_okf(parsed_dict)
                okf_dict = json.loads(raw_json)
                
                # The Validator: Bouncer checks for required fields
                if "tables" not in okf_dict:
                    raise ValueError("Missing 'tables' array in output.")
                
                for table in okf_dict["tables"]:
                    required_table_keys = ["grain", "status", "provenance"]
                    for key in required_table_keys:
                        if key not in table:
                            raise ValueError(f"Table '{table.get('name', 'Unknown')}' is missing required key: {key}")
                    
                    for col in table.get("columns", []):
                        required_col_keys = ["status", "provenance", "pii"]
                        for key in required_col_keys:
                            if key not in col:
                                raise ValueError(f"Column '{col.get('name', 'Unknown')}' is missing required key: {key}")

                okf_yaml = yaml.dump(okf_dict, sort_keys=False, default_flow_style=False)
                
                st.success("OKF Drafted Successfully!")
                st.code(okf_yaml, language="yaml")
                
            except json.JSONDecodeError:
                st.error("LLM failed to return strict JSON. Try again.")
            except ValueError as ve:
                st.error(f"Validation Failed (Spec Drift): {ve}")
            except Exception as e:
                st.error(f"Error: {e}")
