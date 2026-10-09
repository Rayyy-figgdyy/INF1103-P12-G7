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

Every request attaches the owner's manual (PDF) for the user's car, and
the AI must base its urgency score and confidence on that manual.

Backup: if the Gemini call fails (outage, timeout, rate limit, no key),
the same prompt goes to Groq instead. Groq's model reads text only, so the
manual's text is extracted from the PDF and sent with the prompt.

Config (.env): GEMINI_API_KEY, GROQ_API_KEY (at least one is needed),
GEMINI_MODEL and GROQ_MODEL (optional).
"""
from __future__ import annotations

import json
import logging
import os
import time

import pdfplumber
from dotenv import load_dotenv
from google import genai
from groq import Groq

logger = logging.getLogger("ai_manager")

DEFAULT_MODEL = "gemini-3.6-flash"  # the model car_model_identifier.ipynb ran successfully with

# temperature 0 -> the same input gives the same answer across runs
# (hard constraint C4); JSON mime type -> structured output (constraint C3).
GENERATION_CONFIG = {
    "temperature": 0,
    "response_mime_type": "application/json",
    # We never give the AI functions to call, so switch this feature off.
    "automatic_function_calling": {"disable": True},
}

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
API_TIMEOUT_SECONDS = 60
# Groq's free tier allows 8,000 tokens per request (prompt + answer), so the
# backup gets only the manual lines about the part being checked, capped
# at about 2,000 tokens.
MAX_MANUAL_CHARS = 8_000
GROQ_MAX_ANSWER_TOKENS = 2048
# Wait before retrying when both AIs failed (e.g. Gemini 503 "overloaded").
RETRY_DELAY_SECONDS = 3

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
    "manual_reference": _is_non_empty_str,
}

# Merged into the record when the AI can't give a valid answer after
# retrying, so the pipeline keeps going (logic_manager routes it to review).
FALLBACK_ASSESSMENT = {
    "urgency_score": None,
    "confidence": "Unknown",
    "overdue_summary": "AI assessment unavailable.",
    "recommended_action": "Could not get an AI recommendation -- check this part manually.",
    "manual_reference": "n/a",
    "ai_provider": "none",
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
    # Give up on a request after 60 seconds instead of waiting forever.
    return genai.Client(api_key=api_key, http_options={"timeout": API_TIMEOUT_SECONDS * 1000})


def _get_model() -> str:
    load_dotenv()
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def _get_groq_client():
    """The backup Groq client from GROQ_API_KEY, or None if no key is set."""
    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key, timeout=API_TIMEOUT_SECONDS)


def _get_groq_model() -> str:
    load_dotenv()
    return os.environ.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL


def _clients_from_env() -> tuple:
    """(Gemini client, Groq client) from .env; either may be None."""
    return _get_client(), _get_groq_client()


# --- Prompts -------------------------------------------------------------

def build_prompt(record: dict) -> str:
    """Prompt for one maintenance check. The car's owner's manual is
    attached to the same request; the AI must base its answer on it."""
    return (
        "You are an automotive maintenance advisor in Singapore. The owner's "
        "manual for this car is attached. Using that manual as your main "
        "source, assess how urgently one part needs servicing.\n\n"
        "VEHICLE\n"
        f"- Model: {record.get('car_model')}\n"
        f"- Condition when bought: {record.get('car_condition')}\n"
        f"- COE expiry date: {record.get('coe_expiry_date')}\n\n"
        "PART\n"
        f"- Part: {record.get('part_name')}\n"
        f"- Our stored schedule: every {record.get('interval_km')} km or "
        f"{record.get('interval_months')} months, whichever comes first\n"
        f"- Last serviced: {record.get('last_service_date')} at "
        f"{record.get('last_service_mileage')} km\n"
        f"- Today: {record.get('check_date')}, odometer {record.get('current_odometer')} km\n\n"
        "STEPS\n"
        "1. Find what the manual says about this part: its replacement or "
        "inspection interval, and any 'severe driving conditions' schedule "
        "(Singapore's heat and stop-and-go traffic usually count as severe).\n"
        "2. Compare the distance AND time since the last service with that interval.\n"
        "3. Score urgency with this scale:\n"
        "   1-3  well within the interval (under 70% of distance and time used)\n"
        "   4-5  approaching the interval (70-100% used)\n"
        "   6-7  at or just past the interval, part not safety-critical\n"
        "   8-9  clearly overdue, or a safety-critical part (brake pads, "
        "brake fluid) at or past its interval\n"
        "   10   safety-critical and far overdue: do not drive until serviced\n"
        "   For a used car, the earlier service history is unknown, so lean "
        "one point higher when unsure.\n"
        "4. Set confidence:\n"
        "   High    the manual states an interval for this exact part and you used it\n"
        "   Medium  the manual only covers it indirectly (e.g. 'inspect' not "
        "'replace', unclear normal vs severe), or disagrees with our stored schedule\n"
        "   Low     the manual does not cover this part; you used general knowledge\n\n"
        "Reply with ONLY one JSON object, exactly these keys:\n"
        "{\n"
        '  "urgency_score": <integer 1-10>,\n'
        '  "confidence": <"High", "Medium" or "Low">,\n'
        '  "overdue_summary": <one short sentence such as "Engine oil: 650 km overdue" '
        'or "Brake fluid: 12 days overdue" or "Air filter: 3,000 km remaining">,\n'
        '  "recommended_action": <one short sentence telling the owner what to do next>,\n'
        '  "manual_reference": <where in the manual you found this, e.g. '
        '"Maintenance schedule, p. 7", or "Not covered in manual">\n'
        "}"
    )


# --- API call, parsing, validation --------------------------------------------

def _call_gemini(prompt: str, manual_path: str | None, client) -> str | None:
    """Gemini, with the manual PDF uploaded alongside the prompt."""
    try:
        contents = [prompt]
        if manual_path:
            contents = [client.files.upload(file=manual_path), prompt]
        response = client.models.generate_content(
            model=_get_model(), contents=contents, config=GENERATION_CONFIG,
        )
        return response.text
    except Exception as exc:  # noqa: BLE001 -- any API failure must degrade gracefully
        logger.error("Gemini call failed: %s", exc)
        return None


def _manual_text(manual_path: str) -> str:
    """The text of the manual PDF, for the text-only backup model. Returns
    "" (logged) if the PDF can't be read or has no text layer (a scan)."""
    try:
        with pdfplumber.open(manual_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except Exception as exc:  # noqa: BLE001 -- a bad PDF must not crash the app
        logger.error("Could not read text from %s: %s", manual_path, exc)
        return ""
    if not text:
        logger.warning("No text found in %s (is it a scanned image?).", manual_path)
    return text


def _manual_excerpt(text: str, part_name: str) -> str:
    """Only the manual lines that mention the part (plus the line before and
    after, since schedule tables often split across lines), capped at
    MAX_MANUAL_CHARS. Falls back to the start of the manual if no line
    mentions the part."""
    words = [w.lower().rstrip("s") for w in part_name.split() if len(w) > 2]
    lines = text.splitlines()
    keep = set()
    for i, line in enumerate(lines):
        if any(word in line.lower() for word in words):
            keep.update({i - 1, i, i + 1})
    excerpt = "\n".join(lines[i] for i in sorted(keep) if 0 <= i < len(lines)) or text
    if len(excerpt) > MAX_MANUAL_CHARS:
        logger.warning("Manual text cut to %d characters for the backup AI.", MAX_MANUAL_CHARS)
        excerpt = excerpt[:MAX_MANUAL_CHARS]
    return excerpt


def _call_groq(prompt: str, manual_path: str | None, client, part_name: str = "") -> str | None:
    """Groq backup. The relevant manual text is placed before the prompt,
    because this model can't read PDF files directly."""
    content = prompt
    if manual_path:
        manual = _manual_excerpt(_manual_text(manual_path), part_name)
        if manual:
            content = ("OWNER'S MANUAL (the sections about this part, extracted from the PDF):\n"
                       + manual + "\n\n" + prompt)
        else:
            content = "(The owner's manual could not be read for this request.)\n\n" + prompt
    try:
        response = client.chat.completions.create(
            model=_get_groq_model(),
            messages=[{"role": "user", "content": content}],
            temperature=0,               # same input -> same answer (constraint C4)
            max_completion_tokens=GROQ_MAX_ANSWER_TOKENS,  # reasoning + the short JSON
            reasoning_effort="low",      # keeps the request inside the free-tier limit
            stream=False,
        )
        return response.choices[0].message.content
    except Exception as exc:  # noqa: BLE001 -- any API failure must degrade gracefully
        logger.error("Groq backup call failed: %s", exc)
        return None


def _call_with_backup(prompt: str, manual_path: str | None, client, backup_client,
                      part_name: str = "") -> tuple[str | None, str]:
    """Try Gemini, then Groq. Returns (raw reply or None, who answered)."""
    if client is not None:
        raw = _call_gemini(prompt, manual_path, client)
        if raw is not None:
            return raw, "Gemini"
    if backup_client is not None:
        logger.warning("Gemini unavailable -- trying the Groq backup.")
        raw = _call_groq(prompt, manual_path, backup_client, part_name)
        if raw is not None:
            return raw, "Groq (backup)"
    return None, "none"


def call_api(prompt: str, manual_path: str | None = None, client=None,
             backup_client=None) -> str | None:
    """Send the prompt (plus the car's manual, if given) to Gemini, falling
    back to Groq if Gemini fails. Returns the raw reply text, or None if
    both fail. Never raises.

    `client` and `backup_client` are only passed in by tests, to use fakes
    instead of the network. Normally both come from the keys in .env.
    """
    if client is None and backup_client is None:
        client, backup_client = _clients_from_env()
    return _call_with_backup(prompt, manual_path, client, backup_client)[0]


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


def _request_valid_json(prompt: str, validator, manual_path: str | None,
                        client, backup_client, max_attempts: int,
                        part_name: str = "") -> dict | None:
    """call -> parse -> validate, retrying on a failed call or malformed
    output. If both AIs failed, waits briefly first, because errors like
    Gemini's 503 "overloaded" usually clear within seconds."""
    for attempt in range(1, max_attempts + 1):
        raw, provider = _call_with_backup(prompt, manual_path, client, backup_client, part_name)
        result = validator(parse_response(raw))
        if result is not None:
            result["ai_provider"] = provider
            return result
        logger.warning("No valid AI response (attempt %d of %d).", attempt, max_attempts)
        if raw is None and attempt < max_attempts:
            time.sleep(RETRY_DELAY_SECONDS)
    return None


# --- Entry points used by main.py ----------------------------------------------

def process(record: dict, manual_path: str | None = None, client=None,
            backup_client=None, max_attempts: int = 2) -> dict:
    """Send one maintenance record (plus its car's manual) through the AI
    and return a copy with the AI fields merged in. Always returns a
    usable record."""
    if client is None and backup_client is None:
        client, backup_client = _clients_from_env()
    result = _request_valid_json(build_prompt(record), validate_response,
                                 manual_path, client, backup_client, max_attempts,
                                 record.get("part_name", ""))
    return {**record, **(result or FALLBACK_ASSESSMENT)}