from flask import Flask, jsonify, request, render_template, redirect, session, url_for
import re

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

from werkzeug.security import check_password_hash, generate_password_hash

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
USERS_FILE = os.path.join(BASE_DIR, "users.json")

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
    "pension": "senior_citizen",
    "farmer": "farmer",
}


REQUIRED_FIELDS = {
    "age": "Age Group",
    "income": "Annual Income",
    "occupation": "Occupation",
}

ASSISTANT_CATEGORY_HINTS = {
    "Education": ["scholarship", "student", "college", "school", "tuition", "fees", "exam"],
    "Agriculture": ["farmer", "crop", "kisan", "agriculture", "farming", "soil"],
    "Health": ["health", "hospital", "medical", "treatment", "insurance"],
    "Housing": ["housing", "house", "home", "awas"],
    "Employment": ["job", "employment", "business", "loan", "enterprise", "vendor"],
    "Skill Development": ["skill", "training", "certificate", "apprenticeship"],
    "Women & Child": ["girl", "women", "woman", "pregnant", "maternity", "child"],
    "Senior Citizen": ["senior", "old age", "elderly", "pension", "retired"],
    "Social Security": ["pension", "social security", "retirement"],
}

ASSISTANT_INCOME_PHRASES = {
    "low income": 250000,
    "bpl": 250000,
    "middle income": 800000,
    "high income": 1000000000000,
}


ASSISTANT_OCCUPATION_HINTS = {
    "student": "student",
    "college": "student",
    "school": "student",
    "farmer": "farmer",
    "agricultural": "farmer",
    "shetkari": "farmer",
    "daily wage": "daily_wage_worker",
    "worker": "daily_wage_worker",
    "labour": "daily_wage_worker",
    "labor": "daily_wage_worker",
    "self employed": "self_employed",
    "self-employed": "self_employed",
    "business": "self_employed",
    "entrepreneur": "self_employed",
    "vendor": "self_employed",
    "salaried": "salaried_employee",
    "employee": "salaried_employee",
    "job": "salaried_employee",
    "unemployed": "unemployed",
    "jobless": "unemployed",
    "housewife": "unemployed",
    "homemaker": "unemployed",
    "retired": "senior_citizen",
    "senior citizen": "senior_citizen",
}



def normalize_text(value):
    return (value or "").strip().lower()


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def truncate_text(value, max_length=140):
    cleaned = (value or "").strip()
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length] + "..."


def normalize_occupation(value):
    normalized = normalize_text(value)
    return OCCUPATION_NORMALIZATION.get(normalized, normalized)


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def load_user_store():
    if not os.path.exists(USERS_FILE):
        return {"users": []}

    try:
        with open(USERS_FILE, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
    except (json.JSONDecodeError, OSError):
        logger.exception("Users file invalid or unreadable at %s", USERS_FILE)
        return {"users": []}

    if not isinstance(data, dict):
        return {"users": []}

    users = data.get("users")
    if not isinstance(users, list):
        data["users"] = []
    return data


def save_user_store(store):
    users = store.get("users", [])
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as file_handle:
        json.dump({"users": users}, file_handle, indent=2, ensure_ascii=False)


def find_user_by_email(store, email):
    email_text = normalize_text(email)
    for user in store.get("users", []):
        if normalize_text(user.get("email")) == email_text:
            return user
    return None


def find_user_by_id(store, user_id):
    for user in store.get("users", []):
        if user.get("id") == user_id:
            return user
    return None


def append_login_history(user):
    login_event = {
        "timestamp": now_utc_iso(),
        "ip_address": get_client_ip(),
        "user_agent": request.headers.get("User-Agent", "unknown"),
    }
    user.setdefault("login_history", []).append(login_event)
    user["last_login_at"] = login_event["timestamp"]


def append_activity_entry(user, action, details=None):
    entry = {
        "timestamp": now_utc_iso(),
        "action": action,
        "details": details or {},
    }
    user.setdefault("activities", []).append(entry)


def persist_user_activity(user_id, action, details=None):
    store = load_user_store()
    user = find_user_by_id(store, user_id)
    if not user:
        return
    append_activity_entry(user, action, details)
    save_user_store(store)


def sanitize_user(user):
    return {
        "id": user.get("id"),
        "name": user.get("name") or user.get("email"),
        "email": user.get("email"),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
        "login_history": user.get("login_history", []),
        "activities": user.get("activities", []),
    }


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    store = load_user_store()
    user = find_user_by_id(store, user_id)
    if not user:
        session.clear()
        return None

    return sanitize_user(user)


def require_login(view_function):
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("home"))
        return view_function(*args, **kwargs)

    return wrapped


def render_auth_page(active_tab="signin", error_message=None, status_code=200):
    return (
        render_template(
            "auth.html",
            active_tab=active_tab,
            error_message=error_message,
        ),
        status_code,
    )


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
        "You are KAU AI assistant helping users discover Indian and Maharashtra government schemes. "
        "Give concise but practical responses with eligibility reasoning, suggest 2-4 relevant schemes, "
        "and include official apply links when possible. If details are missing, ask for age, occupation, "
        "and annual income. If user asks complex comparison, answer in clear points."
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
        "generationConfig": {"temperature": 0.25, "maxOutputTokens": 360},
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



def amount_from_unit(raw_value, raw_unit):
    cleaned_value = str(raw_value).replace(",", "").strip()
    if not cleaned_value:
        return None
    try:
        amount = float(cleaned_value)
    except ValueError:
        return None

    unit = normalize_text(raw_unit)
    if unit in {"lakh", "lac", "lakhs"}:
        amount *= 100000
    elif unit == "crore":
        amount *= 10000000
    elif unit in {"k", "thousand"}:
        amount *= 1000
    return int(amount)


def format_rupees(amount):
    return f"₹{int(amount):,}"


def infer_category_from_message(message_text):
    best_category = None
    best_score = 0
    for category, keywords in ASSISTANT_CATEGORY_HINTS.items():
        score = sum(1 for keyword in keywords if keyword in message_text)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


def extract_age_from_message(message_text):
    patterns = [
        r"\b(?:i am|i'm|age|aged)\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:years?|yrs?)\s*old\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, message_text)
        if not match:
            continue
        age = int(match.group(1))
        if 0 <= age <= 120:
            return age

    if any(keyword in message_text for keyword in ("senior citizen", "retired", "old age")):
        return 60
    if any(keyword in message_text for keyword in ("school student", "minor child", "under 18")):
        return 16
    return None


def extract_income_cap_from_message(message_text):
    for phrase, amount in ASSISTANT_INCOME_PHRASES.items():
        if phrase in message_text:
            return amount

    patterns = [
        r"(?:income|salary|earning|earnings)\D{0,20}(?:below|under|less than|upto|up to)?\s*₹?\s*(\d+(?:\.\d+)?)\s*(lakh|lac|lakhs|crore|k|thousand)?",
        r"(?:below|under|less than|upto|up to)\s*₹?\s*(\d+(?:\.\d+)?)\s*(lakh|lac|lakhs|crore|k|thousand)\s*(?:annual\s*)?(?:income|salary|earning|earnings)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message_text)
        if not match:
            continue
        value, unit = match.group(1), match.group(2) or ""
        amount = amount_from_unit(value, unit)
        if amount:
            return amount

    return None


def extract_profile_hints(message_text):
    occupation = None
    occupation_hits = [
        (len(keyword), mapped_occupation)
        for keyword, mapped_occupation in ASSISTANT_OCCUPATION_HINTS.items()
        if keyword in message_text
    ]
    if occupation_hits:
        occupation_hits.sort(key=lambda row: row[0], reverse=True)
        occupation = occupation_hits[0][1]

    return {
        "occupation": occupation,
        "age": extract_age_from_message(message_text),
        "income_cap": extract_income_cap_from_message(message_text),
        "category": infer_category_from_message(message_text),
    }


def scheme_is_eligible_for_profile(scheme, profile):
    try:
        scheme_min_age = int(scheme.get("min_age"))
        scheme_max_age = int(scheme.get("max_age"))
        scheme_max_income = int(scheme.get("max_income"))
    except (TypeError, ValueError):
        return False

    profile_age = profile.get("age")
    if profile_age is not None and not (scheme_min_age <= profile_age <= scheme_max_age):
        return False

    income_cap = profile.get("income_cap")
    if income_cap is not None and int(income_cap) > scheme_max_income:
        return False

    desired_occupation = profile.get("occupation")
    scheme_occupation = normalize_occupation(scheme.get("occupation"))
    if desired_occupation and scheme_occupation and desired_occupation != scheme_occupation:
        return False

    return True


def score_scheme_for_message(scheme, message_terms, message_text, profile):
    name = normalize_text(scheme.get("name", ""))
    benefit = normalize_text(scheme.get("benefit", ""))
    category = normalize_text(scheme.get("category", ""))
    source = normalize_text(scheme.get("source", ""))
    scheme_occupation = normalize_occupation(scheme.get("occupation", ""))
    score = 0

    for term in message_terms:
        if term in name:
            score += 3
        if term in benefit:
            score += 1
        if term in category:
            score += 2
        if term in source:
            score += 1

    if profile.get("occupation") and profile.get("occupation") == scheme_occupation:
        score += 6
    elif scheme_occupation and scheme_occupation in message_text:
        score += 2

    if profile.get("category") and normalize_text(profile["category"]) == category:
        score += 4

    if profile.get("age") is not None:
        score += 1
    if profile.get("income_cap") is not None:
        score += 1
    if scheme.get("apply_url"):
        score += 1

    return score


def format_assistant_scheme_row(index, scheme):
    scheme_name = scheme.get("name", "Unnamed Scheme")
    scheme_benefit = scheme.get("benefit", "Benefit information unavailable")
    scheme_category = scheme.get("category", "General")
    apply_url = scheme.get("apply_url", "Official portal unavailable")
    source = scheme.get("source", "Official source")
    return (
        f"{index}. {scheme_name} ({scheme_category})\n"
        f"   Benefit: {scheme_benefit}\n"
        f"   Apply: {apply_url}\n"
        f"   Source: {source}"
    )


def format_profile_summary(profile):
    summary_parts = []
    if profile.get("occupation"):
        summary_parts.append(f"occupation: {profile['occupation'].replace('_', ' ')}")
    if profile.get("age") is not None:
        summary_parts.append(f"age: {profile['age']}")
    if profile.get("income_cap") is not None:
        summary_parts.append(f"income up to {format_rupees(profile['income_cap'])}")
    if profile.get("category"):
        summary_parts.append(f"focus: {profile['category']}")
    return ", ".join(summary_parts)


def suggest_schemes_from_message(message, max_results=5, profile=None):
    message_text = normalize_text(message)
    schemes = load_schemes()
    if not message_text or not schemes:
        return []

    profile = profile or extract_profile_hints(message_text)
    message_terms = {term for term in re.findall(r"[a-z0-9]+", message_text) if len(term) >= 3}
    ranked_matches = []

    for scheme in schemes:
        if not scheme_is_eligible_for_profile(scheme, profile):
            continue
        score = score_scheme_for_message(scheme, message_terms, message_text, profile)
        if score > 0:
            ranked_matches.append((score, scheme))

    if not ranked_matches and profile.get("category"):
        preferred_category = normalize_text(profile["category"])
        for scheme in schemes:
            if normalize_text(scheme.get("category", "")) == preferred_category:
                ranked_matches.append((2, scheme))

    ranked_matches.sort(key=lambda row: (row[0], row[1].get("name", "")), reverse=True)
    return [scheme for _, scheme in ranked_matches[:max_results]]


def build_assistant_reply(message):
    message_text = normalize_text(message)
    if not message_text:
        return "Please type a question about schemes, eligibility, or how to use the form."

    gemini_response = get_gemini_reply(message_text)
    if gemini_response:
        return gemini_response

    if re.search(r"\b(hello|hi|hey|namaste)\b", message_text):
        return (
            "Hello! Share your age, occupation, and annual income, and I will suggest the best schemes "
            "with direct official apply links."
        )

    profile = extract_profile_hints(message_text)
    suggested = suggest_schemes_from_message(message_text, max_results=5, profile=profile)

    if any(keyword in message_text for keyword in ("document", "documents", "paper", "papers", "certificate", "proof")):
        lines = [
            "For most scheme applications, keep these ready:",
            "- Aadhaar card",
            "- Income certificate",
            "- Domicile/residence proof",
            "- Bank passbook copy",
            "- Category certificate (if applicable)",
            "- Passport-size photo",
        ]
        if suggested:
            lines.append("Start with these likely schemes:")
            for idx, scheme in enumerate(suggested[:3], start=1):
                lines.append(format_assistant_scheme_row(idx, scheme))
        return "\n".join(lines)

    if any(keyword in message_text for keyword in ("compare", "difference", "better option", "vs")) and len(suggested) >= 2:
        first = suggested[0]
        second = suggested[1]
        return (
            "Quick comparison:\n"
            f"1) {first.get('name', 'Scheme 1')} - {first.get('benefit', 'Benefit info unavailable')}\n"
            f"   Apply: {first.get('apply_url', 'Official portal unavailable')}\n"
            f"2) {second.get('name', 'Scheme 2')} - {second.get('benefit', 'Benefit info unavailable')}\n"
            f"   Apply: {second.get('apply_url', 'Official portal unavailable')}\n"
            "Choose based on your eligibility and the benefit type you need most."
        )

    if any(keyword in message_text for keyword in ("how", "apply", "registration", "form", "steps")):
        lines = [
            "Application flow:",
            "1. Check eligibility (age, occupation, income).",
            "2. Keep core documents ready (ID, income, bank details).",
            "3. Open the official apply portal for your selected scheme.",
            "4. Complete online form and upload documents.",
            "5. Save acknowledgement number and track status.",
        ]
        if suggested:
            lines.append("Suggested schemes for your query:")
            for idx, scheme in enumerate(suggested[:3], start=1):
                lines.append(format_assistant_scheme_row(idx, scheme))
        return "\n".join(lines)

    if suggested:
        lines = ["Based on your profile/query, these schemes are most relevant:"]
        for idx, scheme in enumerate(suggested, start=1):
            lines.append(format_assistant_scheme_row(idx, scheme))
        profile_summary = format_profile_summary(profile)
        if profile_summary:
            lines.append(f"Profile inferred: {profile_summary}.")
        lines.append("Tip: Verify latest deadlines and required documents on each official portal before applying.")
        return "\n".join(lines)

    return (
        "I need a bit more detail to suggest accurate schemes. Please include your age (or age group), "
        "occupation, annual income, and preferred category (education/health/agriculture/housing)."
    )



def load_schemes():
    try:
        with open(SCHEMES_FILE, "r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
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
    current_user = get_current_user()
    if not current_user:
        return render_auth_page()
    return render_template(
        "index.html",
        ai_agent_api_key_required=bool(AI_AGENT_API_KEY),
        current_user=current_user,
    )


@app.route("/signup", methods=["POST"])
def sign_up():
    name = request.form.get("name", "").strip()
    email = normalize_text(request.form.get("email", ""))
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not name or not email or not password or not confirm_password:
        return render_auth_page(
            active_tab="signup",
            error_message="Please fill all sign-up fields.",
            status_code=400,
        )
    if password != confirm_password:
        return render_auth_page(
            active_tab="signup",
            error_message="Password and confirm password do not match.",
            status_code=400,
        )
    if len(password) < 6:
        return render_auth_page(
            active_tab="signup",
            error_message="Password must be at least 6 characters.",
            status_code=400,
        )

    store = load_user_store()
    if find_user_by_email(store, email):
        return render_auth_page(
            active_tab="signup",
            error_message="An account already exists with this email.",
            status_code=409,
        )

    new_user = {
        "id": str(uuid4()),
        "name": name,
        "email": email,
        "password_hash": generate_password_hash(password),
        "created_at": now_utc_iso(),
        "last_login_at": None,
        "login_history": [],
        "activities": [],
    }

    append_login_history(new_user)
    append_activity_entry(new_user, "sign_up", {"email": email})
    append_activity_entry(new_user, "sign_in", {"method": "password"})
    store.setdefault("users", []).append(new_user)
    save_user_store(store)

    session["user_id"] = new_user["id"]
    logger.info("New user signed up: %s", email)
    return redirect(url_for("home"))


@app.route("/signin", methods=["POST"])
def sign_in():
    email = normalize_text(request.form.get("email", ""))
    password = request.form.get("password", "")

    if not email or not password:
        return render_auth_page(
            active_tab="signin",
            error_message="Please enter email and password.",
            status_code=400,
        )

    store = load_user_store()
    user = find_user_by_email(store, email)
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        logger.info("Invalid login attempt for email: %s", email)
        return render_auth_page(
            active_tab="signin",
            error_message="Invalid email or password.",
            status_code=401,
        )

    append_login_history(user)
    append_activity_entry(user, "sign_in", {"method": "password"})
    save_user_store(store)
    session["user_id"] = user["id"]
    logger.info("User signed in: %s", email)
    return redirect(url_for("home"))


@app.route("/logout", methods=["GET"])
def sign_out():
    current_user = get_current_user()
    if current_user:
        persist_user_activity(current_user["id"], "sign_out")
    session.clear()
    return redirect(url_for("home"))


@app.route("/history", methods=["GET"])
@require_login
def history():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("home"))

    login_history = list(reversed(current_user.get("login_history", [])))
    activities = list(reversed(current_user.get("activities", [])))
    return render_template(
        "history.html",
        current_user=current_user,
        login_history=login_history,
        activities=activities,
    )


@app.route("/result", methods=["POST"])
@require_login
def show_result():
    current_user = get_current_user()
    validation_error = validate_form_data(request.form)
    if validation_error:
        logger.info("Form validation failed: %s", validation_error)
        if current_user:
            persist_user_activity(
                current_user["id"],
                "scheme_search_validation_failed",
                {"error": validation_error},
            )
        return render_template("result.html", schemes=[], error_message=validation_error), 400

    matches = get_matching_schemes(request.form)
    if current_user:
        persist_user_activity(
            current_user["id"],
            "scheme_search",
            {
                "age": request.form.get("age", ""),
                "income": request.form.get("income", ""),
                "occupation": request.form.get("occupation", ""),
                "matched_schemes": len(matches),
            },
        )
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
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Please sign in to use the assistant."}), 401

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
    persist_user_activity(
        current_user["id"],
        "assistant_query",
        {"message": truncate_text(message, max_length=180)},
    )
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
