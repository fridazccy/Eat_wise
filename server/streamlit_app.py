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

# Sidebar: user profile & dietary restrictions
st.sidebar.header("User profile")
gender = st.sidebar.radio("Gender", ["Male", "Female"])
age = st.sidebar.selectbox(
    "Age group",
    ["0-6", "7-12", "13-18", "19-24", "25-34", "35-49", "50-59", ">60"],
)
DIETARY_OPTIONS = [
    "Dairy-free",
    "Gluten-free",
    "Nut-free",
    "Vegetarian",
    "Vegan",
    "Halal",
    "Kosher",
    "Others",
]


base_prompt = """
You are a nutrition assistant. Given a meal description, return a single JSON object with:
- items: array of {name, quantity_text, estimated_grams (optional), calories_estimate, protein_g, carbs_g, fat_g, estimated}
- totals: {calories, protein_g, carbs_g, fat_g}
- suggestions: array of short suggestions
Return ONLY valid JSON.
"""


def build_prompt(meal_text: str, gender: str, age_group: str, restrictions_list: list):
    # Add user context and explicit restriction enforcement instructions
    ctx = f"User profile: gender={gender}, age_group={age_group}."
    if restrictions_list:
        ctx += " Dietary restrictions: " + ", ".join(restrictions_list) + "."
        # Add more explicit guidance so the model avoids restricted items.
        ctx += " When producing items and suggestions, AVOID foods that violate the listed dietary restrictions. "\
               "For example, if 'Vegetarian' is listed, do NOT include meat, fish, poultry, or seafood. "\
               "If 'Vegan' is listed, also avoid dairy, eggs, and honey. "\
               "If 'Nut-free' is listed, avoid nuts and nut-containing foods. "\
               "If 'Gluten-free' is listed, avoid wheat, bread, pasta, and other gluten-containing ingredients. "\
               "If 'Dairy-free' is listed, avoid milk, cheese, butter, yogurt, and similar dairy ingredients. "\
               "If 'Halal' or 'Kosher' are listed, avoid pork and other explicitly forbidden foods for those diets. "\
               "If 'Others' is listed, assume no automatic restrictions unless specified in the meal description."
    else:
        ctx += " No dietary restrictions."

    # Combine with the meal
    system_message = base_prompt + "\n\n" + ctx
    return system_message


def call_azure(meal, gender, age_group, restrictions_list):
    system_prompt = build_prompt(meal, gender, age_group, restrictions_list)
    url = f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={AZURE_API_VERSION}"
    headers = {"Content-Type": "application/json", "api-key": AZURE_API_KEY}
    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": meal},
        ],
        "max_tokens": 800,
        "temperature": 0.2,
    }
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


# Post-processing: simple keyword-based restriction enforcement (safety net)
RESTRICTION_KEYWORDS = {
    "Vegetarian": [
        "chicken",
        "beef",
        "pork",
        "fish",
        "salmon",
        "tuna",
        "turkey",
        "lamb",
        "bacon",
        "ham",
        "sausage",
        "shrimp",
        "seafood",
        "meat",
        "duck",
        "veal",
        "venison",
        "anchovy",
    ],
    "Vegan": [
        # vegan includes vegetarian keywords
        "chicken",
        "beef",
        "pork",
        "fish",
        "salmon",
        "tuna",
        "turkey",
        "lamb",
        "bacon",
        "ham",
        "sausage",
        "shrimp",
        "seafood",
        "meat",
        "duck",
        "veal",
        "venison",
        "anchovy",
        # plus animal products
        "milk",
        "cheese",
        "butter",
        "yogurt",
        "cream",
        "egg",
        "eggs",
        "honey",
    ],
    "Dairy-free": ["milk", "cheese", "butter", "yogurt", "cream", "ice cream"],
    "Gluten-free": [
        "bread",
        "wheat",
        "pasta",
        "flour",
        "beer",
        "barley",
        "rye",
        "seitan",
        "breadcrumbs",
        "croutons",
    ],
    "Nut-free": [
        "almond",
        "peanut",
        "peanuts",
        "cashew",
        "walnut",
        "pecan",
        "hazelnut",
        "brazil nut",
        "macadamia",
        "nut",
    ],
    "Halal": ["pork", "alcohol", "wine", "beer"],
    "Kosher": ["pork", "shrimp", "crab", "lobster", "shellfish"],
    # "Others": no built-in keywords
}


def violates_restrictions(item_name: str, restrictions_list: list) -> bool:
    if not item_name or not restrictions_list:
        return False
    name = item_name.lower()
    for r in restrictions_list:
        keywords = RESTRICTION_KEYWORDS.get(r, [])
        for kw in keywords:
            if kw in name:
                # Special-case: avoid false positives like "donut" matching "nut"
                if kw == "nut" and "donut" in name:
                    continue
                return True
    return False


def filter_items_by_restrictions(parsed: dict, restrictions_list: list) -> (dict, list):
    """
    Remove items that clearly violate restrictions (keyword-based).
    Returns modified parsed dict and a list of removed item names.
    """
    removed = []
    if not isinstance(parsed, dict):
        return parsed, removed
    items = parsed.get("items")
    if not isinstance(items, list):
        return parsed, removed

    kept = []
    for it in items:
        name = it.get("name", "") if isinstance(it, dict) else ""
        if violates_restrictions(name, restrictions_list):
            removed.append(name or json.dumps(it))
        else:
            kept.append(it)

    parsed["items"] = kept

    # Recompute totals simply by summing numeric fields across remaining items if possible
    try:
        totals = {"calories": 0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        any_numeric = False
        for it in kept:
            if not isinstance(it, dict):
                continue
            if "calories_estimate" in it and isinstance(it["calories_estimate"], (int, float)):
                totals["calories"] += it["calories_estimate"]
                any_numeric = True
            if "protein_g" in it and isinstance(it["protein_g"], (int, float)):
                totals["protein_g"] += it["protein_g"]
                any_numeric = True
            if "carbs_g" in it and isinstance(it["carbs_g"], (int, float)):
                totals["carbs_g"] += it["carbs_g"]
                any_numeric = True
            if "fat_g" in it and isinstance(it["fat_g"], (int, float)):
                totals["fat_g"] += it["fat_g"]
                any_numeric = True
        if any_numeric:
            parsed["totals"] = {
                "calories": round(totals["calories"], 2),
                "protein_g": round(totals["protein_g"], 2),
                "carbs_g": round(totals["carbs_g"], 2),
                "fat_g": round(totals["fat_g"], 2),
            }
    except Exception:
        pass

    return parsed, removed


# Main UI: meal input and analyze button
meal = st.text_area(
    "Describe your meal",
    "One chicken Caesar salad with dressing, a medium apple, and a cup of coffee.",
    height=140,
)

if st.button("Analyze"):
    with st.spinner("Analyzing..."):
        try:
            resp = call_azure(meal, gender, age, restrictions)
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = extract_json(content)

            # Enforce restrictions as a safety net (filter out violating items)
            parsed_filtered, removed_items = filter_items_by_restrictions(parsed, restrictions)

            st.subheader("Parsed JSON")
            st.json(parsed_filtered)

            if removed_items:
                st.warning(
                    "The following items were removed from the results because they conflict with the selected dietary restrictions: "
                    + ", ".join(removed_items)
                )

            st.subheader("Raw output")
            st.code(content)
        except requests.exceptions.HTTPError as http_err:
            st.error(f"HTTP error: {http_err}")
        except Exception as e:
            st.error(f"Error: {e}")
