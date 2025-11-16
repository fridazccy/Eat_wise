import os
import re
import json
import requests
import streamlit as st
import pandas as pd

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
restrictions = st.sidebar.multiselect("Dietary Restrictions (optional)", DIETARY_OPTIONS)

# ---- Core prompts / helpers ----
base_prompt = """
You are a nutrition assistant. Given a meal description, return a single JSON object with:
- items: array of {name, quantity_text, estimated_grams (optional), calories_estimate, protein_g, carbs_g, fat_g}
- totals: {calories, protein_g, carbs_g, fat_g}
- suggestions: array of short suggestions
Return ONLY valid JSON.
"""

def build_prompt(meal_text: str, gender: str, age_group: str, restrictions_list: list):
    # Add user context and explicit restriction enforcement instructions
    ctx = f"User profile: gender={gender}, age_group={age_group}."
    if restrictions_list:
        ctx += " Dietary restrictions: " + ", ".join(restrictions_list) + "."
        ctx += " When producing items and suggestions, AVOID foods that violate the listed dietary restrictions. "\
               "For example, if 'Vegetarian' is listed, do NOT include meat, fish, poultry, or seafood. "\
               "If 'Vegan' is listed, also avoid dairy, eggs, and honey. "\
               "If 'Nut-free' is listed, avoid nuts and nut-containing foods. "\
               "If 'Gluten-free' is listed, avoid wheat, bread, pasta, and other gluten-containing ingredients. "\
               "If 'Dairy-free' is listed, avoid milk, cheese, butter, yogurt, and similar dairy ingredients. "\
               "If 'Halal' or 'Kosher' is listed, avoid pork and other explicitly forbidden foods for those diets. "\
               "If 'Others' is listed, assume no automatic restrictions unless specified in the meal description."
    else:
        ctx += " No dietary restrictions."

    # Add explicit instructions to tailor suggestions to age & gender
    age_gender_guidance = (
        "Additionally, provide a short list (3-5) of food suggestions tailored to the user's age group and gender. "
        "Be specific and practical: for children (0-6, 7-12) recommend softer, easy-to-eat, nutrient-dense finger foods and pediatric-appropriate portions; "
        "for teenagers and young adults (13-24) recommend balanced meals with adequate protein and energy for growth and activity; "
        "for adults (25-49) recommend balanced portion sizes and varied nutrients; "
        "for older adults (50-59, >60) recommend softer, easy-to-chew, nutrient-dense foods higher in protein, calcium, and fiber and emphasize hydration and foods that support digestion. "
        "When applicable, mention small practical serving examples (e.g., 'soft cooked salmon flaked over mashed sweet potato') and avoid recommending foods that contradict dietary restrictions. "
        "Also include any small swaps to make common items more appropriate (e.g., 'use mashed avocado instead of butter' for softer texture)."
    )

    system_message = base_prompt + "\n\n" + ctx + "\n\n" + age_gender_guidance
    return system_message


def build_recipe_prompt(meal_type: str, gender: str, age_group: str, restrictions_list: list):
    # Prompt the model to return structured JSON with recipes
    ctx = f"User profile: gender={gender}, age_group={age_group}. Requested meal type: {meal_type}."
    if restrictions_list:
        ctx += " Dietary restrictions: " + ", ".join(restrictions_list) + "."
        ctx += " AVOID foods that violate the listed dietary restrictions."
    else:
        ctx += " No dietary restrictions."

    # Recipe JSON schema instruction
    recipe_schema = """
You are a recipe suggestion assistant. Return ONLY valid JSON with the following structure:
{
  "recipes": [
    {
      "name": "<recipe name>",
      "servings": "<e.g. 1 serving>",
      "prep_time": "<e.g. 15 mins>",
      "ingredients": ["ingredient 1", "ingredient 2", ...],
      "steps": ["step 1", "step 2", ...],
      "notes": "<short suitability notes, e.g., 'soft food, high protein'>"
    },
    ...
  ]
}
Provide 3 recipe suggestions appropriate for the meal type (breakfast/lunch/dinner) and tailored to the user's age and gender. Keep ingredient lists concise and practical. Avoid recommending items that violate dietary restrictions.
"""
    return ctx + "\n\n" + recipe_schema


def call_azure_general(system_prompt: str, user_content: str):
    url = f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={AZURE_API_VERSION}"
    headers = {"Content-Type": "application/json", "api-key": AZURE_API_KEY}
    body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
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
            except Exception:
                return {"raw": text}
        return {"raw": text}


# Post-processing: simple keyword-based restriction enforcement (safety net)
RESTRICTION_KEYWORDS = {
    "Vegetarian": [
        "chicken", "beef", "pork", "fish", "salmon", "tuna", "turkey", "lamb",
        "bacon", "ham", "sausage", "shrimp", "seafood", "meat", "duck", "veal",
        "venison", "anchovy",
    ],
    "Vegan": [
        "chicken", "beef", "pork", "fish", "salmon", "tuna", "turkey", "lamb",
        "bacon", "ham", "sausage", "shrimp", "seafood", "meat", "duck", "veal",
        "venison", "anchovy", "milk", "cheese", "butter", "yogurt", "cream",
        "egg", "eggs", "honey",
    ],
    "Dairy-free": ["milk", "cheese", "butter", "yogurt", "cream", "ice cream"],
    "Gluten-free": [
        "bread", "wheat", "pasta", "flour", "beer", "barley", "rye", "seitan",
        "breadcrumbs", "croutons",
    ],
    "Nut-free": [
        "almond", "peanut", "peanuts", "cashew", "walnut", "pecan", "hazelnut",
        "brazil nut", "macadamia", "nut",
    ],
    "Halal": ["pork", "alcohol", "wine", "beer"],
    "Kosher": ["pork", "shrimp", "crab", "lobster", "shellfish"],
}


def violates_restrictions(item_name: str, restrictions_list: list) -> bool:
    if not item_name or not restrictions_list:
        return False
    name = item_name.lower()
    for r in restrictions_list:
        keywords = RESTRICTION_KEYWORDS.get(r, [])
        for kw in keywords:
            if kw in name:
                if kw == "nut" and "donut" in name:
                    continue
                return True
    return False


def filter_items_by_restrictions(parsed: dict, restrictions_list: list) -> (dict, list):
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
                "calories": round(totals["calories"], 1),
                "protein_g": round(totals["protein_g"], 1),
                "carbs_g": round(totals["carbs_g"], 1),
                "fat_g": round(totals["fat_g"], 1),
            }
    except Exception:
        pass

    return parsed, removed


# Helpers for numeric parsing / formatting
def parse_float_safe(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        if re.match(r"^-?\d+(\.\d+)?$", s):
            try:
                return float(s)
            except Exception:
                return None
    return None


def format_one_decimal_str(v):
    t = parse_float_safe(v)
    if t is None:
        return str(v) if (v is not None and v != "") else ""
    return f"{t:.1f}"


def item_has_nutrition(it: dict) -> bool:
    if not isinstance(it, dict):
        return False
    for key in ("calories_estimate", "protein_g", "carbs_g", "fat_g"):
        v = it.get(key)
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str) and v.strip() != "":
            if re.match(r"^-?\d+(\.\d+)?$", v.strip()):
                return True
    return False


# ---- UI: Tabs ----
tab1, tab2 = st.tabs(["Analyze", "Recipes"])

with tab1:
    # Main UI: meal input and analyze button
    meal = st.text_area(
        "Describe your meal",
        "One chicken Caesar salad with dressing, a medium apple, and a cup of coffee.",
        height=140,
    )

    if st.button("Analyze"):
        # initialize these variables up-front so we can always show the removed/missing lists at the bottom
        removed_items = []
        missing_nutrition = []
        with st.spinner("Analyzing..."):
            try:
                resp = call_azure_general(build_prompt(meal, gender, age, restrictions), meal)
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = extract_json(content)

                # Enforce restrictions as a safety net (filter out violating items)
                parsed_filtered, removed_items = filter_items_by_restrictions(parsed, restrictions)

                # Present nutrition table instead of a long JSON list
                st.subheader("Nutrition Analysis")
                shown_rows = []

                if isinstance(parsed_filtered, dict) and isinstance(parsed_filtered.get("items"), list):
                    items = parsed_filtered.get("items", [])
                    # Build a dataframe with chosen columns (exclude Estimated column)
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        name = it.get("name", "") or ""
                        # If the item has no numeric nutrition fields at all, treat as missing
                        if not item_has_nutrition(it):
                            missing_nutrition.append(name or json.dumps(it))
                            continue

                        # Parse numeric values safely
                        cal = parse_float_safe(it.get("calories_estimate"))
                        prot = parse_float_safe(it.get("protein_g"))
                        carbs = parse_float_safe(it.get("carbs_g"))
                        fat = parse_float_safe(it.get("fat_g"))

                        # If all four numeric nutrition values are present and equal to 0 (or parsed as 0.0), treat as missing and remove the row
                        cal_zero = (cal is not None and cal == 0.0)
                        prot_zero = (prot is not None and prot == 0.0)
                        carbs_zero = (carbs is not None and carbs == 0.0)
                        fat_zero = (fat is not None and fat == 0.0)
                        keys_present = all(k in it for k in ("calories_estimate", "protein_g", "carbs_g", "fat_g"))
                        if keys_present and cal_zero and prot_zero and carbs_zero and fat_zero:
                            missing_nutrition.append(name or json.dumps(it))
                            continue

                        # Format numeric values to strings with 1 decimal place (or keep estimated_grams as-is)
                        shown_rows.append(
                            {
                                "Name": name,
                                "Quantity": it.get("quantity_text", ""),
                                "Estimated grams": it.get("estimated_grams", ""),
                                "Calories": format_one_decimal_str(it.get("calories_estimate")),
                                "Protein (g)": format_one_decimal_str(it.get("protein_g")),
                                "Carbs (g)": format_one_decimal_str(it.get("carbs_g")),
                                "Fat (g)": format_one_decimal_str(it.get("fat_g")),
                            }
                        )

                    # If no items have nutrition details, show message and still continue (we will show removed/missing lists below)
                    if not shown_rows:
                        if missing_nutrition:
                            st.error(
                                "Data can't support the result: the following items do not have nutrition details in the database and could not be analyzed: "
                                + ", ".join(missing_nutrition)
                            )
                        else:
                            st.error("Data can't support the result: no analyzable nutrition items were returned.")
                    else:
                        df = pd.DataFrame(shown_rows)
                        # Set index to start at 1 to show ranking beginning from 1
                        df.index = pd.RangeIndex(start=1, stop=1 + len(df))
                        df.index.name = "No."
                        st.table(df)

                        # Show totals as a compact vertical table (formatted strings with 1 decimal)
                        totals = parsed_filtered.get("totals")
                        if isinstance(totals, dict):
                            totals_row = {
                                "Calories": format_one_decimal_str(totals.get("calories")),
                                "Protein (g)": format_one_decimal_str(totals.get("protein_g")),
                                "Carbs (g)": format_one_decimal_str(totals.get("carbs_g")),
                                "Fat (g)": format_one_decimal_str(totals.get("fat_g")),
                            }
                            df_totals = pd.DataFrame.from_dict(totals_row, orient="index", columns=["Total"])
                            st.subheader("Totals")
                            st.table(df_totals)
                else:
                    st.error("Could not parse nutrition items from the assistant response.")

                # Display suggestions
                suggestions = []
                if isinstance(parsed_filtered, dict):
                    suggestions = parsed_filtered.get("suggestions") or []
                st.subheader("Suggestions")
                if suggestions and isinstance(suggestions, list):
                    for s in suggestions:
                        st.write(f"- {s}")
                else:
                    st.info(
                        "No explicit suggestions were returned by the assistant. The assistant is asked to provide 3-5 food suggestions tailored to the user's age and gender."
                    )

                # --- ALWAYS show the removed-items warning and the missing-nutrition info at the BOTTOM ---
                # Show removed items for restrictions if any (first)
                if removed_items:
                    st.warning(
                        "The following items were removed from the results because they conflict with the selected dietary restrictions: "
                        + ", ".join(removed_items)
                    )

                # Place the missing-nutrition message at the bottom of the page (below removed-items)
                if missing_nutrition:
                    st.info(
                        "The following items could not be analyzed because nutrition details are not available: "
                        + ", ".join(missing_nutrition)
                    )

            except requests.exceptions.HTTPError as http_err:
                st.error(f"HTTP error: {http_err}")
            except Exception as e:
                st.error(f"Error: {e}")


with tab2:
    st.header("Recipe Recommendations")
    st.write("Choose a meal type to get 3 recipe suggestions tailored to your age, gender, and dietary restrictions.")

    # Meal type buttons placed horizontally
    col_b, col_l, col_d = st.columns(3)
    recipe_type = None
    with col_b:
        if st.button("Breakfast"):
            recipe_type = "breakfast"
    with col_l:
        if st.button("Lunch"):
            recipe_type = "lunch"
    with col_d:
        if st.button("Dinner"):
            recipe_type = "dinner"

    # Optionally allow free-text hints
    hint = st.text_input("Optional: add a preference or ingredient you'd like included (e.g., 'no soy', 'quick', 'kid-friendly')")

    if recipe_type:
        with st.spinner(f"Generating {recipe_type} recipes..."):
            try:
                system_prompt = build_recipe_prompt(recipe_type, gender, age, restrictions)
                user_content = hint or f"Please suggest 3 {recipe_type} recipes."
                resp = call_azure_general(system_prompt, user_content)
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = extract_json(content)

                # parsed expected to contain {"recipes": [ ... ]}
                if isinstance(parsed, dict) and isinstance(parsed.get("recipes"), list):
                    recipes = parsed.get("recipes")
                    for idx, r in enumerate(recipes, start=1):
                        name = r.get("name", f"Recipe {idx}")
                        servings = r.get("servings", "")
                        prep_time = r.get("prep_time", "")
                        ingredients = r.get("ingredients", []) or []
                        steps = r.get("steps", []) or []
                        notes = r.get("notes", "")

                        st.subheader(f"{idx}. {name}")
                        if servings:
                            st.write(f"Servings: {servings} | Prep: {prep_time}")
                        if notes:
                            st.caption(notes)
                        if ingredients:
                            st.write("Ingredients:")
                            for ing in ingredients:
                                st.write(f"- {ing}")
                        if steps:
                            st.write("Steps:")
                            for i, step in enumerate(steps, start=1):
                                st.write(f"{i}. {step}")
                        st.markdown("---")
                else:
                    # If not structured JSON, show raw text (but do not reveal secrets)
                    st.info("Couldn't parse structured recipes from assistant. Showing raw assistant answer below.")
                    st.write(content)

            except requests.exceptions.HTTPError as http_err:
                st.error(f"HTTP error: {http_err}")
            except Exception as e:
                st.error(f"Error: {e}")
