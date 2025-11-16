import os
import re
import json
import requests
import streamlit as st

# dotenv is optional so the app won't crash if python-dotenv is not installed
try:
    from dotenv import load_dotenv
    _HAVE_DOTENV = True
except Exception:
    _HAVE_DOTENV = False

from pathlib import Path

# Load local env (server/api.env) if present and python-dotenv is available
env_path = Path(__file__).parent / "api.env"
if _HAVE_DOTENV and env_path.exists():
    load_dotenv(dotenv_path=env_path)

def get_secret(name, default=None):
    try:
        val = st.secrets.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.getenv(name, default)

AZURE_API_KEY = get_secret("AZURE_API_KEY")
AZURE_API_VERSION = get_secret("AZURE_API_VERSION", "2023-05-15")
AZURE_ENDPOINT = get_secret("AZURE_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = get_secret("AZURE_OPENAI_DEPLOYMENT")

st.set_page_config(page_title="Eat Wise — Simple Nutrition", layout="wide")
st.title("Eat Wise — Simple Nutrition")

if not AZURE_API_KEY or not AZURE_ENDPOINT or not AZURE_OPENAI_DEPLOYMENT:
    st.error("Missing Azure config. Create server/api.env or add secrets in Streamlit Cloud.")
    st.stop()

prompt = """
You are a nutrition assistant. Given a meal description, return a single JSON object with:
- items: array of {name, quantity_text, estimated_grams (optional), calories_estimate, protein_g, carbs_g, fat_g, estimated}
- totals: {calories, protein_g, carbs_g, fat_g}
- suggestions: array of short suggestions
Return ONLY valid JSON.
"""

def call_azure(meal):
    url = f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={AZURE_API_VERSION}"
    headers = {"Content-Type":"application/json","api-key":AZURE_API_KEY}
    body = {"messages":[{"role":"system","content":prompt},{"role":"user","content":meal}],"max_tokens":800,"temperature":0.2}
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    return r.json()

def extract_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except:
                return {"raw": text}
        return {"raw": text}

meal = st.text_area("Describe your meal", "One chicken Caesar salad with dressing, a medium apple, and a cup of coffee.", height=140)
if st.button("Analyze"):
    with st.spinner("Analyzing..."):
        try:
            resp = call_azure(meal)
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            st.subheader("Parsed JSON")
            st.json(extract_json(content))
            st.subheader("Raw output")
            st.code(content)
        except Exception as e:
            st.error(f"Error: {e}")
