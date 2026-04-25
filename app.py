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
SAMPLE_NOTIFICATIONS = [
    {
        "id": "notif-1",
        "type": "latest_update",
        "type_label": "Latest Update",
        "title": "PMAY-U Maharashtra portal updated",
        "message": "Income document checklist has been revised for 2026 applications.",
        "date": "2026-04-22",
        "is_new": True,
    },
    {
        "id": "notif-2",
        "type": "new_launch",
        "type_label": "New Launch",
        "title": "Digital Skill Boost Yojana announced",
        "message": "New short-term stipend-backed digital training scheme for youth.",
        "date": "2026-04-20",
        "is_new": True,
    },
    {
        "id": "notif-3",
        "type": "deadline",
        "type_label": "Deadline Reminder",
        "title": "Post-Matric Scholarship deadline",
        "message": "Apply before 30 April 2026 to avoid late submission issues.",
        "date": "2026-04-19",
        "is_new": False,
    },
    {
        "id": "notif-4",
        "type": "announcement",
        "type_label": "Announcement",
        "title": "Service maintenance advisory",
        "message": "Scheme verification services may be slower on Sunday 10 PM-12 AM.",
        "date": "2026-04-18",
        "is_new": False,
    },
]



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
def get_notification_feed(max_items=6):
    notifications = sorted(
        SAMPLE_NOTIFICATIONS,
        key=lambda row: row.get("date", ""),
        reverse=True,
    )
    if max_items is None:
        return notifications
    return notifications[:max_items]


def get_notification_context(max_items=6):
    notifications = get_notification_feed(max_items=max_items)
    unread_count = sum(1 for item in notifications if item.get("is_new"))
    return {
        "notifications": notifications,
        "notification_unread_count": unread_count,
    }


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


def ensure_saved_schemes_list(user):
    saved_items = user.get("saved_schemes")
    if not isinstance(saved_items, list):
        saved_items = []

    normalized_items = []
    seen_names = set()
    for raw_item in saved_items:
        if isinstance(raw_item, dict):
            scheme_name = (raw_item.get("scheme_name") or raw_item.get("name") or "").strip()
            saved_at = raw_item.get("saved_at")
        else:
            scheme_name = str(raw_item).strip()
            saved_at = None

        if not scheme_name:
            continue

        normalized_name = normalize_text(scheme_name)
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        normalized_items.append(
            {
                "scheme_name": scheme_name,
                "saved_at": saved_at,
            }
        )

    user["saved_schemes"] = normalized_items
    return normalized_items


def build_scheme_lookup():
    lookup = {}
    for scheme in load_schemes():
        scheme_name = (scheme.get("name") or "").strip()
        if not scheme_name:
            continue
        lookup[normalize_text(scheme_name)] = scheme
    return lookup


def get_saved_scheme_names_for_user(user_id):
    store = load_user_store()
    user = find_user_by_id(store, user_id)
    if not user:
        return set()

    saved_items = ensure_saved_schemes_list(user)
    return {normalize_text(item.get("scheme_name", "")) for item in saved_items if item.get("scheme_name")}


def get_saved_schemes_for_user(user_id):
    store = load_user_store()
    user = find_user_by_id(store, user_id)
    if not user:
        return []

    saved_items = ensure_saved_schemes_list(user)
    scheme_lookup = build_scheme_lookup()
    enriched_items = []
    for item in saved_items:
        scheme_name = (item.get("scheme_name") or "").strip()
        if not scheme_name:
            continue
        saved_at = item.get("saved_at")
        matched_scheme = scheme_lookup.get(normalize_text(scheme_name))
        if matched_scheme:
            scheme_data = {**matched_scheme}
        else:
            scheme_data = {
                "name": scheme_name,
                "benefit": "Saved scheme record. Official details are currently unavailable.",
                "category": "General",
                "occupation": "Not specified",
                "min_age": "N/A",
                "max_age": "N/A",
                "max_income": 0,
                "apply_url": "",
                "source": "Saved Scheme",
            }
        scheme_data["saved_at"] = saved_at
        enriched_items.append(scheme_data)

    enriched_items.sort(key=lambda row: row.get("saved_at") or "", reverse=True)
    return enriched_items


def save_scheme_for_user(user_id, scheme_name):
    scheme_name = (scheme_name or "").strip()
    if not scheme_name:
        return {"ok": False, "error": "Scheme name is required."}

    scheme_lookup = build_scheme_lookup()
    normalized_name = normalize_text(scheme_name)
    scheme_data = scheme_lookup.get(normalized_name)
    if not scheme_data:
        return {"ok": False, "error": "Scheme not found."}

    canonical_name = scheme_data.get("name", scheme_name)
    store = load_user_store()
    user = find_user_by_id(store, user_id)
    if not user:
        return {"ok": False, "error": "User not found."}

    saved_items = ensure_saved_schemes_list(user)
    for item in saved_items:
        if normalize_text(item.get("scheme_name", "")) == normalized_name:
            return {
                "ok": True,
                "saved": True,
                "already_saved": True,
                "total_saved": len(saved_items),
                "scheme_name": canonical_name,
            }

    saved_items.append(
        {
            "scheme_name": canonical_name,
            "saved_at": now_utc_iso(),
        }
    )
    user["saved_schemes"] = saved_items
    save_user_store(store)
    return {
        "ok": True,
        "saved": True,
        "already_saved": False,
        "total_saved": len(saved_items),
        "scheme_name": canonical_name,
    }


def remove_saved_scheme_for_user(user_id, scheme_name):
    scheme_name = (scheme_name or "").strip()
    if not scheme_name:
        return {"ok": False, "error": "Scheme name is required."}

    normalized_name = normalize_text(scheme_name)
    store = load_user_store()
    user = find_user_by_id(store, user_id)
    if not user:
        return {"ok": False, "error": "User not found."}

    saved_items = ensure_saved_schemes_list(user)
    remaining_items = []
    removed = False
    for item in saved_items:
        if normalize_text(item.get("scheme_name", "")) == normalized_name:
            removed = True
            continue
        remaining_items.append(item)

    user["saved_schemes"] = remaining_items
    if removed:
        save_user_store(store)

    return {
        "ok": True,
        "removed": removed,
        "total_saved": len(remaining_items),
        "scheme_name": scheme_name,
    }


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
        "saved_schemes": user.get("saved_schemes", []),
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


@app.route("/saved-schemes", methods=["GET"])
@require_login
def saved_schemes_page():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("home"))

    saved_schemes = get_saved_schemes_for_user(current_user["id"])
    return render_template(
        "saved_schemes.html",
        current_user=current_user,
        saved_schemes=saved_schemes,
        **get_notification_context(),
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

def age_overlap_ratio(user_range, scheme_min_age, scheme_max_age):
    user_min, user_max = user_range
    overlap_min = max(user_min, scheme_min_age)
    overlap_max = min(user_max, scheme_max_age)
    if overlap_min > overlap_max:
        return 0.0
    overlap_span = overlap_max - overlap_min + 1
    user_span = max(1, user_max - user_min + 1)
    return min(1.0, overlap_span / user_span)


def build_match_component(label, score, weight, status, detail):
    return {
        "label": label,
        "score": int(round(score)),
        "weight": int(weight),
        "status": status,
        "detail": detail,
    }


def score_scheme_against_form(scheme, form_data):
    selected_age = form_data.get("age", "").strip()
    selected_income = form_data.get("income", "").strip()
    selected_occupation = normalize_occupation(form_data.get("occupation", ""))
    selected_location = normalize_text(form_data.get("location", ""))
    selected_gender = normalize_text(form_data.get("gender", ""))
    selected_social_category = normalize_text(form_data.get("category", ""))

    try:
        scheme_min_age = int(scheme.get("min_age"))
        scheme_max_age = int(scheme.get("max_age"))
        scheme_max_income = int(scheme.get("max_income"))
    except (TypeError, ValueError):
        return {
            "eligibility_score": 0,
            "eligibility_label": "Low",
            "eligibility_components": [
                build_match_component(
                    "Eligibility Data",
                    0,
                    100,
                    "unavailable",
                    "Scheme has incomplete eligibility criteria.",
                )
            ],
        }

    scheme_occupation = normalize_occupation(scheme.get("occupation"))
    scheme_text = normalize_text(
        f"{scheme.get('name', '')} {scheme.get('benefit', '')} {scheme.get('category', '')}"
    )

    components = []
    total_weight = 0.0
    achieved_score = 0.0

    age_range = AGE_GROUP_TO_RANGE.get(selected_age)
    if age_range:
        age_weight = 35
        overlap = age_overlap_ratio(age_range, scheme_min_age, scheme_max_age)
        age_score = age_weight * overlap
        age_status = "matched" if overlap >= 0.99 else ("partial" if overlap > 0 else "not_matched")
        components.append(
            build_match_component(
                "Age",
                age_score,
                age_weight,
                age_status,
                f"Selected range {age_range[0]}-{age_range[1]}, scheme allows {scheme_min_age}-{scheme_max_age}.",
            )
        )
        total_weight += age_weight
        achieved_score += age_score

    income_cap = INCOME_RANGE_TO_MAX.get(selected_income)
    if income_cap is not None:
        income_weight = 35
        if int(income_cap) <= scheme_max_income:
            income_score = income_weight
            income_status = "matched"
        else:
            affordability_ratio = max(0.0, min(1.0, scheme_max_income / max(1, int(income_cap))))
            income_score = income_weight * (affordability_ratio * 0.55)
            income_status = "partial" if affordability_ratio >= 0.75 else "not_matched"
        components.append(
            build_match_component(
                "Income",
                income_score,
                income_weight,
                income_status,
                f"Selected income cap {format_rupees(income_cap)}, scheme cap {format_rupees(scheme_max_income)}.",
            )
        )
        total_weight += income_weight
        achieved_score += income_score

    if selected_occupation:
        occupation_weight = 30
        if selected_occupation == scheme_occupation:
            occupation_score = occupation_weight
            occupation_status = "matched"
        elif not scheme_occupation:
            occupation_score = occupation_weight * 0.5
            occupation_status = "partial"
        else:
            occupation_score = 0
            occupation_status = "not_matched"
        components.append(
            build_match_component(
                "Occupation",
                occupation_score,
                occupation_weight,
                occupation_status,
                f"Selected {selected_occupation.replace('_', ' ')}, scheme targets {scheme_occupation.replace('_', ' ') if scheme_occupation else 'all occupations'}.",
            )
        )
        total_weight += occupation_weight
        achieved_score += occupation_score

    if selected_location:
        location_weight = 8
        location_keywords = {
            "urban": ("urban", "city", "vendor"),
            "semi_urban": ("urban", "semi-urban", "town"),
            "rural": ("rural", "gramin", "farmer", "village"),
        }
        keywords = location_keywords.get(selected_location, ())
        location_hit = any(keyword in scheme_text for keyword in keywords)
        location_score = location_weight if location_hit else location_weight * 0.35
        components.append(
            build_match_component(
                "Location",
                location_score,
                location_weight,
                "matched" if location_hit else "partial",
                f"Selected {selected_location.replace('_', ' ')}, checked scheme context for location relevance.",
            )
        )
        total_weight += location_weight
        achieved_score += location_score

    if selected_gender and selected_gender != "male":
        gender_weight = 8
        gender_keywords = {
            "female": ("women", "girl", "maternity", "pregnant", "kanya"),
            "transgender": ("transgender", "inclusive", "social justice"),
        }
        keywords = gender_keywords.get(selected_gender, ())
        gender_hit = any(keyword in scheme_text for keyword in keywords)
        gender_score = gender_weight if gender_hit else gender_weight * 0.25
        components.append(
            build_match_component(
                "Gender Focus",
                gender_score,
                gender_weight,
                "matched" if gender_hit else "partial",
                f"Selected {selected_gender}, checked scheme relevance to gender-focused benefits.",
            )
        )
        total_weight += gender_weight
        achieved_score += gender_score

    if selected_social_category and selected_social_category != "general":
        category_weight = 8
        category_keywords = {
            "obc": ("obc",),
            "sc": ("sc ", "scheduled caste"),
            "st": ("st ", "scheduled tribe"),
            "minority": ("minority",),
        }
        keywords = category_keywords.get(selected_social_category, ())
        category_hit = any(keyword in scheme_text for keyword in keywords)
        category_score = category_weight if category_hit else category_weight * 0.25
        components.append(
            build_match_component(
                "Category Focus",
                category_score,
                category_weight,
                "matched" if category_hit else "partial",
                f"Selected {selected_social_category.upper()}, checked mention in scheme details.",
            )
        )
        total_weight += category_weight
        achieved_score += category_score

    if total_weight <= 0:
        return {
            "eligibility_score": 0,
            "eligibility_label": "Low",
            "eligibility_components": [],
        }

    final_score = int(round((achieved_score / total_weight) * 100))
    if final_score >= 80:
        label = "High"
    elif final_score >= 60:
        label = "Moderate"
    else:
        label = "Low"

    return {
        "eligibility_score": max(0, min(100, final_score)),
        "eligibility_label": label,
        "eligibility_components": components,
    }


def get_matching_schemes(form_data):
    scored_schemes = []
    for scheme in load_schemes():
        score_data = score_scheme_against_form(scheme, form_data)
        scored_scheme = {**scheme, **score_data}
        scored_schemes.append(scored_scheme)

    scored_schemes.sort(
        key=lambda row: (row.get("eligibility_score", 0), row.get("max_income", 0), row.get("name", "")),
        reverse=True,
    )

    strong_matches = [scheme for scheme in scored_schemes if scheme.get("eligibility_score", 0) >= 55]
    if strong_matches:
        return strong_matches[:12]

    fallback_matches = [scheme for scheme in scored_schemes if scheme.get("eligibility_score", 0) >= 35]
    return fallback_matches[:8]


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
        **get_notification_context(),
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
        "saved_schemes": [],
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
        **get_notification_context(),
    )


@app.route("/result", methods=["POST"])
@require_login
def show_result():
    current_user = get_current_user()
    saved_scheme_names = (
        get_saved_scheme_names_for_user(current_user["id"])
        if current_user
        else set()
    )
    validation_error = validate_form_data(request.form)
    if validation_error:
        logger.info("Form validation failed: %s", validation_error)
        if current_user:
            persist_user_activity(
                current_user["id"],
                "scheme_search_validation_failed",
                {"error": validation_error},
            )
        return render_template(
            "result.html",
            schemes=[],
            error_message=validation_error,
            current_user=current_user,
            saved_scheme_names=saved_scheme_names,
            **get_notification_context(),
        ), 400

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
    return render_template(
        "result.html",
        schemes=matches,
        error_message=None,
        current_user=current_user,
        saved_scheme_names=saved_scheme_names,
        **get_notification_context(),
    )


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


@app.route("/api/saved-schemes", methods=["GET"])
def list_saved_schemes():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Please sign in to view saved schemes."}), 401

    saved_schemes = get_saved_schemes_for_user(current_user["id"])
    return jsonify(
        {
            "saved_schemes": saved_schemes,
            "saved_scheme_names": [scheme.get("name", "") for scheme in saved_schemes],
            "total_saved": len(saved_schemes),
        }
    ), 200


@app.route("/api/saved-schemes/save", methods=["POST"])
def save_scheme_endpoint():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Please sign in to save schemes."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be JSON with 'scheme_name'."}), 400

    scheme_name = payload.get("scheme_name", "")
    result = save_scheme_for_user(current_user["id"], scheme_name)
    if not result.get("ok"):
        status_code = 404 if "not found" in result.get("error", "").lower() else 400
        return jsonify({"error": result.get("error")}), status_code

    if not result.get("already_saved"):
        persist_user_activity(
            current_user["id"],
            "scheme_saved",
            {"scheme_name": result.get("scheme_name", "")},
        )

    return jsonify(result), 200


@app.route("/api/saved-schemes/remove", methods=["POST"])
def remove_saved_scheme_endpoint():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"error": "Please sign in to remove saved schemes."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be JSON with 'scheme_name'."}), 400

    scheme_name = payload.get("scheme_name", "")
    result = remove_saved_scheme_for_user(current_user["id"], scheme_name)
    if not result.get("ok"):
        return jsonify({"error": result.get("error")}), 400

    if result.get("removed"):
        persist_user_activity(
            current_user["id"],
            "scheme_unsaved",
            {"scheme_name": result.get("scheme_name", "")},
        )

    return jsonify(result), 200


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
