from flask import Flask, jsonify, request, render_template
import json
import logging
import os
import urllib.error
import urllib.request
from env_loader import get_env, load_env_file

# ─────────────────────────────────────────────
#  App Configuration
# ─────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_env_file(os.path.join(BASE_DIR, ".env"), override=False)

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "static"),
    template_folder=os.path.join(BASE_DIR, "templates"),
)

app.config["DEBUG"] = str(get_env("FLASK_DEBUG", "true")).lower() == "true"
app.config["SECRET_KEY"] = get_env("SECRET_KEY", "change-me-in-production")

logging.basicConfig(
    level=str(get_env("LOG_LEVEL", "INFO")).upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("kau.app")
AI_AGENT_API_KEY = get_env("AI_AGENT_API_KEY", "")
GEMINI_API_KEY = get_env("GEMINI_API_KEY", "")

SCHEMES_FILE = os.path.join(BASE_DIR, "schemes.json")

AGE_GROUP_TO_RANGE = {
    "below_18": (0, 17),
    "18_25": (18, 25),
    "26_40": (26, 40),
    "41_60": (41, 60),
    "above_60": (61, 200),
    "Below 18": (0, 17),
    "18 – 25": (18, 25),
    "18 - 25": (18, 25),
    "26 – 40": (26, 40),
    "26 - 40": (26, 40),
    "41 – 60": (41, 60),
    "41 - 60": (41, 60),
    "Above 60": (61, 200),
}

INCOME_RANGE_TO_MAX = {
    "below_1_lakh": 100000,
    "1_to_2_5_lakhs": 250000,
    "2_5_to_5_lakhs": 500000,
    "5_to_10_lakhs": 1000000,
    "above_10_lakhs": 10**12,
    "Below ₹1 Lakh": 100000,
    "₹1 – 2.5 Lakhs": 250000,
    "₹1 - 2.5 Lakhs": 250000,
    "₹2.5 – 5 Lakhs": 500000,
    "₹2.5 - 5 Lakhs": 500000,
    "₹5 – 10 Lakhs": 1000000,
    "₹5 - 10 Lakhs": 1000000,
    "Above ₹10 Lakhs": 10**12,
}

OCCUPATION_NORMALIZATION = {
    "student": "student",
    "farmer_agri_worker": "farmer",
    "daily_wage_worker": "daily_wage_worker",
    "self_employed": "self_employed",
    "salaried_employee": "salaried_employee",
    "unemployed": "unemployed",
    "senior_citizen": "senior_citizen",
    "farmer / agricultural worker": "farmer",
    "self-employed": "self_employed",
    "salaried employee": "salaried_employee",
    "daily wage worker": "daily_wage_worker",
    "senior citizen": "senior_citizen",
    "farmer": "farmer",
}

REQUIRED_FIELDS = {
    "age": "Age Group",
    "income": "Annual Income",
    "occupation": "Occupation",
}

ASSISTANT_OCCUPATION_HINTS = {
    "student": "student",
    "farmer": "farmer",
    "agricultural": "farmer",
    "daily wage": "daily_wage_worker",
    "worker": "daily_wage_worker",
    "self employed": "self_employed",
    "self-employed": "self_employed",
    "salaried": "salaried_employee",
    "job": "salaried_employee",
    "unemployed": "unemployed",
    "senior citizen": "senior_citizen",
}


def normalize_text(value):
    return (value or "").strip().lower()


def normalize_occupation(value):
    normalized = normalize_text(value)
    return OCCUPATION_NORMALIZATION.get(normalized, normalized)


def has_valid_agent_api_key():
    if not AI_AGENT_API_KEY:
        return True

    provided_key = request.headers.get("X-API-Key", "").strip()
    if not provided_key:
        authorization = request.headers.get("Authorization", "").strip()
        if authorization.lower().startswith("bearer "):
            provided_key = authorization[7:].strip()

    return provided_key == AI_AGENT_API_KEY

def get_gemini_reply(message):
    if not GEMINI_API_KEY:
        return None

    prompt = (
        "You are KAU AI assistant helping users discover Indian government schemes. "
        "Reply briefly with practical guidance. If details are missing, ask for age group, "
        "occupation, and income range."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"{prompt}\n\nUser message: {message}\nAssistant response:"
                    }
                ]
            }
        ],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 220},
    }
    request_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    )
    request_body = json.dumps(payload).encode("utf-8")

    try:
        req = urllib.request.Request(
            request_url,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        logger.warning("Gemini API call failed. Falling back to local assistant response.")
        return None

    candidates = response_data.get("candidates") or []
    if not candidates:
        return None

    first_candidate = candidates[0] or {}
    parts = ((first_candidate.get("content") or {}).get("parts")) or []
    if not parts:
        return None

    text = (parts[0] or {}).get("text", "").strip()
    return text or None


def should_return_json_error():
    if request.path.startswith("/api/"):
        return True
    best = request.accept_mimetypes.best
    return best == "application/json" and (
        request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
    )


def validate_form_data(form_data):
    missing_fields = [label for key, label in REQUIRED_FIELDS.items() if not form_data.get(key, "").strip()]
    if missing_fields:
        return f"Please select: {', '.join(missing_fields)}."

    age = form_data.get("age", "").strip()
    income = form_data.get("income", "").strip()
    if age not in AGE_GROUP_TO_RANGE:
        return "Invalid Age Group selection."
    if income not in INCOME_RANGE_TO_MAX:
        return "Invalid Annual Income selection."

    return None


def suggest_schemes_from_message(message, max_results=3):
    message_text = normalize_text(message)
    schemes = load_schemes()
    if not message_text or not schemes:
        return []

    message_terms = {
        term
        for term in message_text.replace("/", " ").replace("-", " ").split()
        if len(term) >= 3
    }
    ranked_matches = []

    for scheme in schemes:
        name = normalize_text(scheme.get("name", ""))
        benefit = normalize_text(scheme.get("benefit", ""))
        scheme_occupation = normalize_occupation(scheme.get("occupation", ""))
        score = 0

        for term in message_terms:
            if term in name:
                score += 2
            if term in benefit:
                score += 1

        if scheme_occupation and scheme_occupation in message_text:
            score += 3

        for keyword, expected_occupation in ASSISTANT_OCCUPATION_HINTS.items():
            if keyword in message_text and scheme_occupation == expected_occupation:
                score += 3

        if score > 0:
            ranked_matches.append((score, scheme))

    ranked_matches.sort(key=lambda row: row[0], reverse=True)
    return [scheme for _, scheme in ranked_matches[:max_results]]


def build_assistant_reply(message):
    message_text = normalize_text(message)
    if not message_text:
        return "Please type a question about schemes, eligibility, or how to use the form."

    gemini_response = get_gemini_reply(message_text)
    if gemini_response:
        return gemini_response

    if any(greet in message_text for greet in ("hello", "hi", "hey", "namaste")):
        return (
            "Hello! I can help you find relevant government schemes. "
            "Tell me your occupation, age group, or income range, and I will suggest options."
        )

    if "how" in message_text and ("use" in message_text or "apply" in message_text or "form" in message_text):
        return (
            "Use the form above by selecting Age Group, Annual Income, and Occupation, then click "
            "'Find My Schemes'. I can also suggest options if you describe your profile in chat."
        )

    suggested = suggest_schemes_from_message(message_text)
    if suggested:
        lines = []
        for scheme in suggested:
            scheme_name = scheme.get("name", "Unnamed Scheme")
            scheme_benefit = scheme.get("benefit", "Benefit information unavailable")
            lines.append(f"- {scheme_name}: {scheme_benefit}")
        return (
            "Based on your message, these schemes may be relevant:\n"
            + "\n".join(lines)
            + "\nYou can submit the main form for a stricter eligibility match."
        )

    return (
        "I could not find a strong match yet. Please include details like occupation "
        "(student/farmer/etc.), age group, and income range."
    )


def load_schemes():
    try:
        with open(SCHEMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                logger.error("Invalid schemes format: expected a JSON array.")
                return []
            return data
    except FileNotFoundError:
        logger.error("Schemes file not found at %s", SCHEMES_FILE)
        return []
    except json.JSONDecodeError:
        logger.exception("Invalid JSON in schemes file: %s", SCHEMES_FILE)
        return []


def get_matching_schemes(form_data):
    selected_age = form_data.get("age", "").strip()
    selected_income = form_data.get("income", "").strip()
    selected_occupation = normalize_occupation(form_data.get("occupation", ""))

    age_range = AGE_GROUP_TO_RANGE.get(selected_age)
    income_cap = INCOME_RANGE_TO_MAX.get(selected_income)

    matches = []
    for scheme in load_schemes():
        try:
            scheme_min_age = int(scheme.get("min_age"))
            scheme_max_age = int(scheme.get("max_age"))
            scheme_max_income = int(scheme.get("max_income"))
        except (TypeError, ValueError):
            logger.warning("Skipping invalid scheme record: %s", scheme)
            continue

        scheme_occupation = normalize_occupation(scheme.get("occupation"))

        if age_range:
            user_min_age, user_max_age = age_range
            if user_max_age < scheme_min_age or user_min_age > scheme_max_age:
                continue

        if income_cap is not None and int(income_cap) > scheme_max_income:
            continue

        if selected_occupation and scheme_occupation and selected_occupation != scheme_occupation:
            continue

        matches.append(scheme)

    return matches


# ─────────────────────────────────────────────
#  Frontend Routes
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html", ai_agent_api_key_required=bool(AI_AGENT_API_KEY))


@app.route("/result", methods=["POST"])
def show_result():
    validation_error = validate_form_data(request.form)
    if validation_error:
        logger.info("Form validation failed: %s", validation_error)
        return render_template("result.html", schemes=[], error_message=validation_error), 400

    matches = get_matching_schemes(request.form)
    logger.info(
        "Scheme search completed: age=%s income=%s occupation=%s matches=%d",
        request.form.get("age", ""),
        request.form.get("income", ""),
        request.form.get("occupation", ""),
        len(matches),
    )
    return render_template("result.html", schemes=matches, error_message=None)


# ─────────────────────────────────────────────
#  REST API Routes
# ─────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "message": "Server is running"}), 200

@app.route("/api/assistant", methods=["POST"])
def assistant_chat():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be JSON with a 'message' field."}), 400
    if not has_valid_agent_api_key():
        logger.warning("Rejected assistant request due to missing/invalid API key.")
        return jsonify({"error": "Invalid or missing API key."}), 401

    message = data.get("message", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Please provide a non-empty 'message' field."}), 400

    reply = build_assistant_reply(message)
    logger.info("Assistant query served with %d characters", len(message.strip()))
    return jsonify({"reply": reply}), 200


@app.route("/api/items", methods=["GET"])
def get_items():
    items = [
        {"id": 1, "name": "Item One", "description": "First sample item"},
        {"id": 2, "name": "Item Two", "description": "Second sample item"},
        {"id": 3, "name": "Item Three", "description": "Third sample item"},
    ]
    return jsonify({"items": items, "total": len(items)}), 200


@app.route("/api/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    item = {"id": item_id, "name": f"Item {item_id}", "description": "Sample item"}
    return jsonify(item), 200


@app.route("/api/items", methods=["POST"])
def create_item():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "'name' field is required"}), 422

    new_item = {"id": 999, "name": name, "description": data.get("description", "")}
    return jsonify(new_item), 201


@app.route("/api/items/<int:item_id>", methods=["PUT"])
def update_item(item_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    updated = {"id": item_id, **data}
    return jsonify(updated), 200


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    return jsonify({"message": f"Item {item_id} deleted successfully"}), 200


# ─────────────────────────────────────────────
#  Global Error Handlers
# ─────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    logger.warning("404 not found: %s", request.path)
    if should_return_json_error():
        return jsonify({"error": "Resource not found"}), 404
    return render_template(
        "error.html",
        status_code=404,
        title="Page not found",
        message="The page you requested does not exist.",
    ), 404


@app.errorhandler(405)
def method_not_allowed(error):
    logger.warning("405 method not allowed: %s %s", request.method, request.path)
    if should_return_json_error():
        return jsonify({"error": "Method not allowed"}), 405
    return render_template(
        "error.html",
        status_code=405,
        title="Method not allowed",
        message="This action is not allowed on the requested page.",
    ), 405


@app.errorhandler(500)
def internal_error(error):
    logger.exception("500 internal server error at path: %s", request.path)
    if should_return_json_error():
        return jsonify({"error": "Internal server error"}), 500
    return render_template(
        "error.html",
        status_code=500,
        title="Internal server error",
        message="Something went wrong on our side. Please try again.",
    ), 500


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
