"""
ai_manager.py -- AI processing layer. The core engine.

Framework brief, section 2.2 -- every record passes through here:
  - build_prompt(record)     clear, specific prompt that asks for JSON
  - call_api(prompt)         send to the API; catch errors/timeouts; log, never crash
  - parse_response(raw)      extract and parse the JSON; handle unexpected formats
  - validate_response(data)  required keys, correct types, values in range
  - No domain logic in this file -- only API interaction and validation.

The test from the brief: delete this file and the app can no longer
produce an urgency score, an overdue summary or a recommendation, so
logic_manager has nothing to route on. That is AI as the core engine.

Also identifies a car model from a photo (proposal: "Car Model (Photo of
the car model or by text)"), adapted from car_model_identifier.ipynb.

Config (.env): GEMINI_API_KEY (required), GEMINI_MODEL (optional).
"""
from __future__ import annotations

import json
import logging
import os

from dotenv import load_dotenv
from google import genai

logger = logging.getLogger("ai_manager")

DEFAULT_MODEL = "gemini-3.6-flash"  # the model car_model_identifier.ipynb ran successfully with

# temperature 0 -> the same input gives the same answer across runs
# (hard constraint C4); JSON mime type -> structured output (constraint C3).
GENERATION_CONFIG = {"temperature": 0, "response_mime_type": "application/json"}

VALID_CONFIDENCE = ("High", "Medium", "Low")


def _is_non_empty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_score(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10


# Schema = required key -> check that its value is acceptable.
ASSESSMENT_SCHEMA = {
    "urgency_score": _is_score,
    "confidence": lambda v: v in VALID_CONFIDENCE,
    "overdue_summary": _is_non_empty_str,
    "recommended_action": _is_non_empty_str,
}

CAR_ID_SCHEMA = {
    "make": _is_non_empty_str,
    "model": _is_non_empty_str,
    "year_range": _is_non_empty_str,
    "confidence": lambda v: v in VALID_CONFIDENCE,
}

# Merged into the record when the AI can't give a valid answer after
# retrying, so the pipeline keeps going (logic_manager routes it to review).
FALLBACK_ASSESSMENT = {
    "urgency_score": None,
    "confidence": "Unknown",
    "overdue_summary": "AI assessment unavailable.",
    "recommended_action": "Could not get an AI recommendation -- check this part manually.",
    "ai_status": "unavailable",
}


# --- Client ------------------------------------------------------------

def _get_client():
    """A Gemini client built from GEMINI_API_KEY, or None (logged) if unset."""
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY is not set -- copy .env.example to .env and add your key.")
        return None
    return genai.Client(api_key=api_key)


def _get_model() -> str:
    load_dotenv()
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


# --- Prompts -------------------------------------------------------------

def build_prompt(record: dict) -> str:
    """Prompt for one maintenance check. Asks for strict JSON only."""
    return (
        "You are an automotive maintenance advisor in Singapore. Assess how "
        "urgently one car part needs servicing.\n\n"
        "VEHICLE\n"
        f"- Model: {record.get('car_model')}\n"
        f"- Condition when bought: {record.get('car_condition')}\n"
        f"- COE expiry date: {record.get('coe_expiry_date')}\n\n"
        "PART\n"
        f"- Part: {record.get('part_name')}\n"
        f"- Manufacturer schedule: every {record.get('interval_km')} km or "
        f"{record.get('interval_months')} months, whichever comes first\n"
        f"- Last serviced: {record.get('last_service_date')} at "
        f"{record.get('last_service_mileage')} km\n"
        f"- Today: {record.get('check_date')}, odometer {record.get('current_odometer')} km\n\n"
        "Consider: how far past (or short of) the schedule the part is by "
        "distance AND by time; how heavily the car is driven since the last "
        "service; whether the part is safety-critical; and that used cars may "
        "have unknown prior wear.\n\n"
        "Reply with ONLY one JSON object, exactly these keys:\n"
        "{\n"
        '  "urgency_score": <integer 1-10; 1 = not urgent, 10 = service immediately>,\n'
        '  "confidence": <"High", "Medium" or "Low">,\n'
        '  "overdue_summary": <one short sentence such as "Engine oil: 650 km overdue" '
        'or "Brake fluid: 12 days overdue" or "Air filter: 3,000 km remaining">,\n'
        '  "recommended_action": <one short sentence telling the owner what to do next>\n'
        "}"
    )


def build_car_id_prompt() -> str:
    """Prompt for identifying a car from a photo (from the notebook)."""
    return (
        "Identify the car in this photo. Reply with ONLY one JSON object, "
        "exactly these keys:\n"
        "{\n"
        '  "make": <manufacturer, e.g. "Toyota">,\n'
        '  "model": <model name, e.g. "Corolla Altis">,\n'
        '  "year_range": <best estimate of year or generation, e.g. "2019-2023">,\n'
        '  "confidence": <"High", "Medium" or "Low">\n'
        "}\n"
        "If you cannot tell the exact model, give your best guess with Low confidence."
    )


# --- API call, parsing, validation --------------------------------------------

def call_api(prompt: str, image_path: str | None = None, client=None) -> str | None:
    """Send the prompt (plus an optional image) to Gemini and return the
    raw text. Any failure -- no key, network, timeout, auth, quota,
    upload -- is logged and returns None. Never raises.

    `client` is only passed in by tests, to use a fake instead of the network.
    """
    if client is None:
        client = _get_client()
        if client is None:
            return None
    try:
        contents = [prompt]
        if image_path:
            contents = [client.files.upload(file=image_path), prompt]
        response = client.models.generate_content(
            model=_get_model(), contents=contents, config=GENERATION_CONFIG,
        )
        return response.text
    except Exception as exc:  # noqa: BLE001 -- any API failure must degrade gracefully
        logger.error("AI API call failed: %s", exc)
        return None


def parse_response(raw: str | None) -> dict | None:
    """Extract the JSON object from the model's reply (tolerates ```json
    fences and stray text around it). None if there's nothing parseable."""
    if not raw or not raw.strip():
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end < start:
        logger.error("No JSON object in AI response: %r", raw[:200])
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        logger.error("AI response is not valid JSON: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _check_schema(data: dict | None, schema: dict) -> dict | None:
    """Keep only the schema's keys if every one is present and valid; else None."""
    if not isinstance(data, dict):
        return None
    cleaned = {}
    for key, is_valid in schema.items():
        if key not in data:
            logger.error("AI response missing key %r: %s", key, data)
            return None
        if not is_valid(data[key]):
            logger.error("AI response has an invalid %r: %r", key, data[key])
            return None
        cleaned[key] = data[key]
    return cleaned


def validate_response(data: dict | None) -> dict | None:
    """Validate a maintenance assessment. Returns the cleaned fields plus
    ai_status='ok', or None to reject it."""
    if isinstance(data, dict):
        score = data.get("urgency_score")
        if isinstance(score, float) and score.is_integer():
            data = {**data, "urgency_score": int(score)}  # accept 7.0 as 7
    cleaned = _check_schema(data, ASSESSMENT_SCHEMA)
    if cleaned is None:
        return None
    cleaned["urgency_score"] = int(cleaned["urgency_score"])
    cleaned["ai_status"] = "ok"
    return cleaned


def validate_car_id_response(data: dict | None) -> dict | None:
    """Validate a photo identification result, or None to reject it."""
    return _check_schema(data, CAR_ID_SCHEMA)


def _request_valid_json(prompt: str, validator, image_path: str | None,
                        client, max_attempts: int) -> dict | None:
    """call -> parse -> validate, retrying on a failed call or malformed output."""
    for attempt in range(1, max_attempts + 1):
        result = validator(parse_response(call_api(prompt, image_path=image_path, client=client)))
        if result is not None:
            return result
        logger.warning("No valid AI response (attempt %d of %d).", attempt, max_attempts)
    return None


# --- Entry points used by main.py ----------------------------------------------

def process(record: dict, client=None, max_attempts: int = 2) -> dict:
    """Send one maintenance record through the AI and return a copy with
    the AI fields merged in. Always returns a usable record."""
    result = _request_valid_json(build_prompt(record), validate_response, None, client, max_attempts)
    return {**record, **(result or FALLBACK_ASSESSMENT)}


def identify_car_model(image_path: str, client=None, max_attempts: int = 2) -> dict | None:
    """Identify a car from a photo: {'make','model','year_range','confidence'},
    or None if the AI couldn't give a valid answer."""
    return _request_valid_json(build_car_id_prompt(), validate_car_id_response,
                               image_path, client, max_attempts)
