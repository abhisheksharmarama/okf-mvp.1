import os
import streamlit as st

PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

def call_llm(prompt: str) -> str:
    if PROVIDER == "claude":
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        r = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text
    else:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.0-flash")
        return model.generate_content(prompt).text

st.title("OKF MVP — Phase 1")
q = st.text_area("Prompt")
if st.button("Send") and q:
    with st.spinner("Thinking..."):
        try:
            st.write(call_llm(q))
        except Exception as e:
            st.error(f"{type(e).__name__}: {e}")
