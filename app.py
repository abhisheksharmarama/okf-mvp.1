import os
import json
import yaml
import re
import streamlit as st
import google.generativeai as genai

st.set_page_config(page_title="OKF MVP", layout="wide")

def parse_input(text: str) -> dict:
    """Parses basic CSV (table,column,type) or naive CREATE TABLE DDL."""
    tables = {}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines: return tables
    
    if "," in lines[0] and not lines[0].lower().startswith("create"):
        # CSV Mode
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                t, c, typ = parts[0], parts[1], parts[2]
                if t not in tables: tables[t] = []
                tables[t].append({"name": c, "type": typ})
    else:
        # Naive DDL Mode
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
    # Temperature 0 and JSON mode forced
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
    - Propose relationships only where column names and types genuinely align. Do not invent joins.
    - Output valid JSON matching the OKF v0.1 schema exactly.
    - No prose, no markdown fences.

    SCHEMA:
    {json.dumps(schema_dict, indent=2)}
    """
    return model.generate_content(prompt).text

st.title("OKF MVP — Phase 2")
st.markdown("Paste your DDL or CSV (`table,column,type`). **Max 30 tables. Nothing is saved or logged.**")

schema_input = st.text_area("Schema Input", height=200)

if st.button("Generate OKF") and schema_input:
    parsed_dict = parse_input(schema_input)
    
    if len(parsed_dict) > 30:
        st.error("Too many tables! Please limit to 30 for this MVP.")
    elif not parsed_dict:
        st.error("Could not parse input. Check formatting.")
    else:
        with st.spinner("Drafting OKF via LLM..."):
            try:
                raw_json = generate_okf(parsed_dict)
                okf_dict = json.loads(raw_json) # Validator: will fail if not valid JSON
                
                # Convert back to YAML for the UI
                okf_yaml = yaml.dump(okf_dict, sort_keys=False, default_flow_style=False)
                
                st.success("OKF Drafted Successfully!")
                st.code(okf_yaml, language="yaml")
                
                st.download_button(
                    label="Download OKF.yaml",
                    data=okf_yaml,
                    file_name="okf_draft.yaml",
                    mime="text/yaml"
                )
            except json.JSONDecodeError:
                st.error("LLM failed to return strict JSON. Try again.")
            except Exception as e:
                st.error(f"Error: {e}")
