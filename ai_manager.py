"""
ai_manager.py -- TALKING TO THE AI (the "AI processing layer").

(New to Python? See the "How to read Python" guide at the top of main.py.)

WHAT THIS FILE DOES
    For each maintenance check, this file:
      1. writes a question for the AI       -> build_prompt()
      2. sends it to the AI, with the car's
         owner's manual attached             -> call_api()
      3. pulls the answer out of the reply   -> parse_response()
      4. checks the answer is complete and
         sensible before anyone uses it      -> validate_response()
    These are the four functions our project brief (section 2.2) asks for.
    process() runs all four steps in order; it's what main.py calls.

WHY THE AI IS THE "CORE ENGINE" (hard constraint C2)
    Every check goes through the AI. Without this file the app could not
    give an urgency score at all, so the business rules would have
    nothing to work with. That's what the brief means by "core engine".

THE BACKUP PLAN
    1. Gemini (Google's AI) gets the question PLUS the manual's PDF file.
    2. If Gemini fails (down, too slow, too busy, no key), the same
       question goes to Groq, our backup AI. Groq can only read text, not
       PDF files, so we pull the relevant text out of the PDF and send that.
    3. If both fail, the app doesn't crash: it fills in "AI unavailable"
       values, and the record's outcome becomes "NEEDS MANUAL REVIEW".

RULES FROM OUR PROJECT BRIEF
    - No business rules here: only talking to the AI and checking its answer.
      (The rules live in logic_manager.py.)
    - Errors are written to the log, never printed (print() is only
      allowed in io_manager.py). "Log and continue, never crash."

SECRET KEYS
    The AI services need passwords called "API keys". They live in a
    private file called .env (never shared or uploaded):
        GEMINI_API_KEY=...      GROQ_API_KEY=...

Phase 2 of the project (object-oriented version): this file becomes an
AIManager class with a process() method.
"""
import json        # reads JSON, the structured text format the AI replies in
import logging     # records errors
import os          # reads settings, such as the API keys
import time        # lets the app wait a few seconds before retrying

import pdfplumber                  # reads the text out of PDF files
from dotenv import load_dotenv     # loads the secret keys from the .env file
from google import genai           # Google's library for Gemini
from groq import Groq              # Groq's library (our backup AI)

# A "logger" records errors and warnings, labelled with this file's name.
logger = logging.getLogger("ai_manager")

# ======================================================================
# FIXED SETTINGS
# ======================================================================

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"      # which Gemini AI model to use
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"     # which Groq AI model to use
API_TIMEOUT_SECONDS = 60     # give up on an AI that hasn't answered within 60 s
RETRY_DELAY_SECONDS = 3      # if both AIs fail, wait 3 s before one more try

# Settings sent to Gemini with every question:
GEMINI_CONFIG = {
    # Temperature 0 = no randomness: the same question always gets the
    # same answer (hard constraint C4: same output across runs).
    "temperature": 0,
    # Force the reply to be JSON, so the app can read it (hard constraint C3).
    "response_mime_type": "application/json",
    # An AI feature we don't use (letting the AI run code). Switched off.
    "automatic_function_calling": {"disable": True},
}

# Groq's free plan only allows about 8,000 "tokens" (word pieces) per
# question. So instead of the whole manual, Groq only gets the lines
# about the part being checked, up to this many characters.
MAX_MANUAL_CHARS = 8_000
GROQ_MAX_ANSWER_TOKENS = 2048     # the most the AI may write back

# Every valid AI answer must contain all five of these pieces of information.
REQUIRED_KEYS = ("urgency_score", "confidence", "overdue_summary",
                 "recommended_action", "manual_reference")
VALID_CONFIDENCE = ("High", "Medium", "Low")

# The values used when NO AI could answer. The app keeps going with
# these instead of crashing, and the business rules then mark the
# record as "NEEDS MANUAL REVIEW".
FALLBACK_ASSESSMENT = {
    "urgency_score": None,
    "confidence": "Unknown",
    "overdue_summary": "AI assessment unavailable.",
    "recommended_action": "Could not get an AI recommendation -- check this part manually.",
    "manual_reference": "n/a",
    "ai_provider": "none",
    "ai_status": "unavailable",
}


# ======================================================================
# CONNECTING TO THE TWO AI SERVICES
# ======================================================================

def _get_clients() -> tuple:
    """Set up the connections ("clients") to Gemini and Groq using the
    keys in the .env file. If a key is missing, that connection is None,
    and the app simply relies on the other one.
    Gives back two things at once: (gemini connection, groq connection)."""
    load_dotenv()                                    # read the .env file
    gemini_key = os.environ.get("GEMINI_API_KEY")    # None if not set
    groq_key = os.environ.get("GROQ_API_KEY")

    gemini = None
    if gemini_key:
        # The timeout for Gemini is given in milliseconds (1 s = 1000 ms).
        gemini = genai.Client(api_key=gemini_key,
                              http_options={"timeout": API_TIMEOUT_SECONDS * 1000})
    else:
        logger.error("GEMINI_API_KEY is not set -- copy .env.example to .env and add your key.")

    groq = None
    if groq_key:
        groq = Groq(api_key=groq_key, timeout=API_TIMEOUT_SECONDS)

    return gemini, groq


# ======================================================================
# STEP 1: build_prompt -- write the question for the AI
# ======================================================================

def build_prompt(record: dict) -> str:
    """Write the question (the "prompt") for one maintenance check.

    It gives the AI the car and part details, tells it to use the
    attached manual, and gives it a FIXED 1-10 urgency scale, so its
    scores line up with the thresholds in our business rules
    (logic_manager.route). It also asks for the reply in JSON only, so
    the app can read it (hard constraint C3).

    The text is built by joining many pieces of text together; the
    f"...{...}" pieces slot in this check's details."""
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


# ======================================================================
# STEP 2: call_api -- send the question to the AI (Gemini, then Groq)
# ======================================================================

def _call_gemini(prompt: str, manual_path: str | None, client) -> str | None:
    """Send the question to Gemini, with the manual's PDF file attached.
    Gives back Gemini's reply text, or None if anything went wrong."""
    try:
        contents = [prompt]                     # what we send: just the question...
        if manual_path:
            # ...or, if we have a manual: first upload the PDF, then send
            # the uploaded file together with the question.
            uploaded_manual = client.files.upload(file=manual_path)
            contents = [uploaded_manual, prompt]
        model = os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        response = client.models.generate_content(model=model, contents=contents, config=GEMINI_CONFIG)
        return response.text
    except Exception as error:
        # ANY problem (no internet, wrong key, too busy "503", too many
        # requests "429", too slow...) lands here. Record it, and give back
        # None so the backup AI can be tried. The app doesn't crash.
        logger.error("Gemini call failed: %s", error)
        return None


def _manual_text(manual_path: str) -> str:
    """Read all the text out of the manual's PDF file (for Groq, which
    can't open PDF files itself). Gives back "" (empty text) if the PDF
    can't be read, or if it's a scanned picture with no real text in it."""
    try:
        page_texts = []
        with pdfplumber.open(manual_path) as pdf:      # open the PDF
            for page in pdf.pages:                     # go through every page
                page_texts.append(page.extract_text() or "")
        text = "\n".join(page_texts).strip()           # join the pages into one text
    except Exception as error:                         # a damaged PDF mustn't crash the app
        logger.error("Could not read text from %s: %s", manual_path, error)
        return ""
    if not text:
        logger.warning("No text found in %s (is it a scanned image?).", manual_path)
    return text


def _manual_excerpt(text: str, part_name: str) -> str:
    """From the manual's full text, keep only the lines about this part
    (plus the line just before and after each one, because schedule
    tables often spread over two lines). This keeps the question to Groq
    small enough for its free plan (otherwise it refuses with error 413).
    If no line mentions the part, it uses the start of the manual instead."""
    # Turn the part name into search words: "Brake Pads" -> ["brake", "pad"]
    # (lower case, plural 's' removed, very short words skipped).
    search_words = []
    for word in part_name.split():
        if len(word) > 2:
            search_words.append(word.lower().rstrip("s"))

    lines = text.splitlines()          # the manual, as a list of lines

    # Find the numbers of the lines to keep. A "set" stores each number
    # once, even if we add it twice.
    lines_to_keep = set()
    for line_number in range(len(lines)):
        line = lines[line_number].lower()
        for word in search_words:
            if word in line:
                # keep this line, the one before and the one after
                lines_to_keep.update({line_number - 1, line_number, line_number + 1})

    # Collect those lines in their original order (skipping numbers that
    # don't exist, like -1 before the first line).
    kept_lines = []
    for line_number in sorted(lines_to_keep):
        if 0 <= line_number < len(lines):
            kept_lines.append(lines[line_number])

    excerpt = "\n".join(kept_lines)
    if not excerpt:                     # the part wasn't mentioned anywhere
        excerpt = text
    if len(excerpt) > MAX_MANUAL_CHARS:
        logger.warning("Manual text cut to %d characters for the backup AI.", MAX_MANUAL_CHARS)
        excerpt = excerpt[:MAX_MANUAL_CHARS]      # keep only the first part
    return excerpt


def _call_groq(prompt: str, manual_path: str | None, client, part_name: str = "") -> str | None:
    """Send the question to Groq (the backup AI), with the manual's
    relevant text placed in front of it.
    Gives back Groq's reply text, or None if anything went wrong."""
    content = prompt
    if manual_path:
        manual = _manual_excerpt(_manual_text(manual_path), part_name)
        if manual:
            content = ("OWNER'S MANUAL (the sections about this part, extracted from the PDF):\n"
                       + manual + "\n\n" + prompt)
        else:
            content = "(The owner's manual could not be read for this request.)\n\n" + prompt
    try:
        model = os.environ.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],   # our question
            temperature=0,                                     # same question -> same answer (C4)
            max_completion_tokens=GROQ_MAX_ANSWER_TOKENS,      # limit the answer's length
            reasoning_effort="low",                            # keeps within the free plan's limit
            stream=False,                                      # wait for the whole answer
        )
        return response.choices[0].message.content             # the reply text
    except Exception as error:                                 # any problem: record it, carry on
        logger.error("Groq backup call failed: %s", error)
        return None


def _ask_ai(prompt: str, manual_path: str | None, gemini, groq, part_name: str = "") -> tuple:
    """Ask Gemini first; if that fails, ask Groq.
    Gives back two things: (the reply text or None, which AI answered)."""
    if gemini is not None:
        reply = _call_gemini(prompt, manual_path, gemini)
        if reply is not None:
            return reply, "Gemini"
    if groq is not None:
        logger.warning("Gemini unavailable -- trying the Groq backup.")
        reply = _call_groq(prompt, manual_path, groq, part_name)
        if reply is not None:
            return reply, "Groq (backup)"
    return None, "none"                 # both failed (or no keys at all)


def call_api(prompt: str, manual_path: str | None = None, client=None, backup_client=None) -> str | None:
    """Send the question (and manual) to the AI and give back its raw reply
    text, or None if every AI failed. Never crashes.

    client and backup_client are only filled in by our automatic tests,
    which use pretend AIs instead of the internet. Normally they are
    left empty and the real connections are made from the .env keys."""
    if client is None and backup_client is None:
        client, backup_client = _get_clients()
    reply, who_answered = _ask_ai(prompt, manual_path, client, backup_client)
    return reply


# ======================================================================
# STEP 3: parse_response -- pull the answer out of the reply
# ======================================================================

def parse_response(reply: str | None) -> dict | None:
    """The AI replies with text that should contain a JSON object, such as
        {"urgency_score": 7, "confidence": "High", ...}
    This finds that part and turns it into a Python dictionary.
    Copes with extra words or ```json marks around it.
    Gives back the dictionary, or None if there's no readable JSON."""
    if not reply or not reply.strip():
        return None
    start = reply.find("{")      # where the first { is
    end = reply.rfind("}")       # where the last } is
    if start == -1 or end < start:          # -1 means "not found"
        logger.error("No JSON object in AI response: %r", reply[:200])
        return None
    try:
        data = json.loads(reply[start:end + 1])   # read the text between { and }
    except json.JSONDecodeError as error:         # it wasn't valid JSON
        logger.error("AI response is not valid JSON: %s", error)
        return None
    if isinstance(data, dict):
        return data
    return None


# ======================================================================
# STEP 4: validate_response -- check the answer before using it
# ======================================================================

def validate_response(data: dict | None) -> dict | None:
    """Check the AI's answer is complete and sensible BEFORE anything uses
    it (hard constraint C3). Gives back a clean copy marked
    ai_status "ok", or None to reject the answer (the app then retries)."""
    if not isinstance(data, dict):
        return None

    # 1. All five required pieces of information must be there.
    for key in REQUIRED_KEYS:
        if key not in data:
            logger.error("AI response is missing %r: %s", key, data)
            return None

    # 2. urgency_score must be a whole number from 1 to 10.
    #    7.0 is accepted as 7. True/False are refused (Python treats
    #    True as 1 and False as 0, which would sneak through otherwise).
    score = data["urgency_score"]
    if isinstance(score, float) and score.is_integer():
        score = int(score)
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
        logger.error("AI response has an invalid urgency_score: %r", data["urgency_score"])
        return None

    # 3. confidence must be exactly High, Medium or Low.
    if data["confidence"] not in VALID_CONFIDENCE:
        logger.error("AI response has an invalid confidence: %r", data["confidence"])
        return None

    # 4. The three text answers must contain real, non-empty text.
    for key in ("overdue_summary", "recommended_action", "manual_reference"):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            logger.error("AI response has an empty or invalid %r: %r", key, value)
            return None

    # Everything checks out: give back a clean copy.
    return {
        "urgency_score": score,
        "confidence": data["confidence"],
        "overdue_summary": data["overdue_summary"],
        "recommended_action": data["recommended_action"],
        "manual_reference": data["manual_reference"],
        "ai_status": "ok",
    }


# ======================================================================
# process -- runs steps 1 to 4 for one check (this is what main.py calls)
# ======================================================================

def process(record: dict, manual_path: str | None = None, client=None,
            backup_client=None, max_attempts: int = 2) -> dict:
    """Run one maintenance check through the AI:
        build the question -> ask the AI -> read the reply -> check it
    If the answer is missing or faulty, try again (up to max_attempts
    times in total).

    Gives back a COPY of the record with the AI's answer added. It always
    gives back something usable: if no AI answers properly, the "AI
    unavailable" values (FALLBACK_ASSESSMENT) are added instead.
    ("Log and continue, never crash.")"""
    if client is None and backup_client is None:
        client, backup_client = _get_clients()

    prompt = build_prompt(record)                       # STEP 1

    # range(1, max_attempts + 1) counts 1, 2, ... up to max_attempts.
    for attempt in range(1, max_attempts + 1):
        reply, who_answered = _ask_ai(prompt, manual_path, client, backup_client,
                                      record.get("part_name", ""))        # STEP 2
        answer = validate_response(parse_response(reply))                  # STEPS 3 + 4

        if answer is not None:                          # a good answer: done
            answer["ai_provider"] = who_answered        # shown as "Answered by"
            result = dict(record)                       # a copy of the record...
            result.update(answer)                       # ...with the AI's answer added
            return result

        logger.warning("No valid AI response (attempt %d of %d).", attempt, max_attempts)
        # If neither AI replied at all, wait a few seconds before trying
        # again: "too busy" errors (like Gemini's 503) often clear quickly.
        if reply is None and attempt < max_attempts:
            time.sleep(RETRY_DELAY_SECONDS)

    # Every attempt failed: carry on with the "AI unavailable" values.
    result = dict(record)
    result.update(FALLBACK_ASSESSMENT)
    return result