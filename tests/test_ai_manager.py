"""
Tests for ai_manager's response handling with hardcoded sample AI
responses and a fake client -- no network, no API key. Covers the
"malformed API response" and "API connection failure" exception cases.

The fake client is a SimpleNamespace (not a class definition), so the
codebase stays 100% procedural.
"""
import json
from types import SimpleNamespace

import ai_manager

ai_manager.RETRY_DELAY_SECONDS = 0  # tests shouldn't wait between retries

GOOD = {
    "urgency_score": 7,
    "confidence": "High",
    "overdue_summary": "Engine oil: 650 km overdue",
    "recommended_action": "Book an oil change this week.",
    "manual_reference": "Maintenance schedule, p. 7",
}

RECORD = {"part_name": "Engine Oil", "car_model": "Toyota Corolla 2020", "current_odometer": 50_650}


def fake_client(replies: list, calls: list):
    """A stand-in for genai.Client. Each generate_content() call returns the
    next reply; a reply that is an Exception is raised instead."""
    def generate_content(model, contents, config):
        calls.append(contents)
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)

    def upload(file):
        return f"<uploaded {file}>"

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content),
                           files=SimpleNamespace(upload=upload))


# --- build_prompt ---------------------------------------------------------------

def test_prompt_includes_record_fields_and_asks_for_json():
    prompt = ai_manager.build_prompt({**RECORD, "interval_km": 10_000, "interval_months": 6})
    assert "Engine Oil" in prompt and "10000 km" in prompt
    assert "JSON" in prompt and "urgency_score" in prompt


def test_prompt_tells_ai_to_use_the_manual_and_a_fixed_scale():
    prompt = ai_manager.build_prompt(RECORD)
    assert "manual" in prompt.lower()
    assert "manual_reference" in prompt
    assert "8-9" in prompt and "High" in prompt  # urgency scale and confidence rules


# --- parse_response ---------------------------------------------------------------

def test_parse_plain_json():
    assert ai_manager.parse_response(json.dumps(GOOD)) == GOOD


def test_parse_json_inside_markdown_fence_and_extra_text():
    raw = "Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps."
    assert ai_manager.parse_response(raw) == GOOD


def test_parse_rejects_non_json():
    assert ai_manager.parse_response("I think the oil is fine.") is None
    assert ai_manager.parse_response("{not: valid json}") is None
    assert ai_manager.parse_response("") is None
    assert ai_manager.parse_response(None) is None


# --- validate_response ---------------------------------------------------------------

def test_validate_accepts_good_response():
    result = ai_manager.validate_response(dict(GOOD))
    assert result["urgency_score"] == 7
    assert result["ai_status"] == "ok"


def test_validate_rejects_missing_key():
    for key in GOOD:
        bad = dict(GOOD)
        del bad[key]
        assert ai_manager.validate_response(bad) is None, key


def test_validate_rejects_out_of_range_or_wrong_type_score():
    for bad_score in (0, 11, "7", True, 7.5, None):
        assert ai_manager.validate_response({**GOOD, "urgency_score": bad_score}) is None


def test_validate_accepts_whole_number_float_score():
    assert ai_manager.validate_response({**GOOD, "urgency_score": 7.0})["urgency_score"] == 7


def test_validate_rejects_unknown_confidence_and_empty_text():
    assert ai_manager.validate_response({**GOOD, "confidence": "Very sure"}) is None
    assert ai_manager.validate_response({**GOOD, "overdue_summary": "  "}) is None


# --- process: retry and graceful failure ----------------------------------------------

def test_process_merges_ai_fields_into_record():
    calls = []
    result = ai_manager.process(RECORD, client=fake_client([json.dumps(GOOD)], calls))
    assert result["urgency_score"] == 7 and result["ai_status"] == "ok"
    assert result["part_name"] == "Engine Oil"  # original fields kept
    assert len(calls) == 1


def test_process_retries_after_malformed_response():
    calls = []
    result = ai_manager.process(RECORD, client=fake_client(["not json", json.dumps(GOOD)], calls))
    assert result["ai_status"] == "ok"
    assert len(calls) == 2


def test_process_falls_back_after_repeated_malformed_responses():
    calls = []
    result = ai_manager.process(RECORD, client=fake_client(['{"urgency_score": 99}'], calls), max_attempts=2)
    assert result["ai_status"] == "unavailable"
    assert result["urgency_score"] is None
    assert len(calls) == 2


def test_process_survives_connection_failure():
    calls = []
    result = ai_manager.process(RECORD, client=fake_client([ConnectionError("network down")], calls))
    assert result["ai_status"] == "unavailable"
    assert result["part_name"] == "Engine Oil"


def test_call_api_returns_none_instead_of_raising():
    assert ai_manager.call_api("hi", client=fake_client([TimeoutError("timed out")], [])) is None


# --- The car's manual is attached to every request -------------------------------

def test_process_attaches_manual_before_prompt():
    calls = []
    result = ai_manager.process(RECORD, manual_path="manuals/kia_forte.pdf",
                                client=fake_client([json.dumps(GOOD)], calls))
    assert calls[0][0] == "<uploaded manuals/kia_forte.pdf>"  # manual first
    assert "Engine Oil" in calls[0][1]                       # then the prompt
    assert result["manual_reference"] == "Maintenance schedule, p. 7"


def test_process_without_manual_sends_prompt_only():
    calls = []
    ai_manager.process(RECORD, client=fake_client([json.dumps(GOOD)], calls))
    assert len(calls[0]) == 1


def test_fallback_has_every_ai_field():
    calls = []
    result = ai_manager.process(RECORD, client=fake_client(["garbage"], calls))
    for key in ai_manager.REQUIRED_KEYS:
        assert key in result


# --- Groq backup when Gemini fails -----------------------------------------------

def fake_groq(replies: list, calls: list):
    """A stand-in for the Groq client: chat.completions.create() returns the
    next reply; a reply that is an Exception is raised instead."""
    def create(model, messages, **kwargs):
        calls.append(messages[-1]["content"])
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


GROQ_ANSWER = {**GOOD, "urgency_score": 6, "manual_reference": "Schedule table (text)"}


def test_gemini_answer_is_used_and_backup_not_called():
    gemini_calls, groq_calls = [], []
    result = ai_manager.process(RECORD, client=fake_client([json.dumps(GOOD)], gemini_calls),
                                backup_client=fake_groq([json.dumps(GROQ_ANSWER)], groq_calls))
    assert result["ai_provider"] == "Gemini"
    assert result["urgency_score"] == 7
    assert groq_calls == []


def test_backup_answers_when_gemini_fails():
    groq_calls = []
    result = ai_manager.process(RECORD, client=fake_client([ConnectionError("down")], []),
                                backup_client=fake_groq([json.dumps(GROQ_ANSWER)], groq_calls))
    assert result["ai_provider"] == "Groq (backup)"
    assert result["urgency_score"] == 6
    assert len(groq_calls) == 1


def test_backup_works_with_no_gemini_key():
    result = ai_manager.process(RECORD, client=None,
                                backup_client=fake_groq([json.dumps(GROQ_ANSWER)], []))
    assert result["ai_provider"] == "Groq (backup)"


def test_manual_review_when_both_fail():
    result = ai_manager.process(RECORD, client=fake_client([TimeoutError("slow")], []),
                                backup_client=fake_groq([ConnectionError("down")], []))
    assert result["ai_status"] == "unavailable"
    assert result["ai_provider"] == "none"


def test_backup_receives_the_manual_text():
    groq_calls = []
    saved = ai_manager.manual_text
    ai_manager.manual_text = lambda path: "Engine oil: replace every 7,500 km"
    try:
        ai_manager.process(RECORD, manual_path="manuals/kia_forte.pdf",
                           client=fake_client([ConnectionError("down")], []),
                           backup_client=fake_groq([json.dumps(GROQ_ANSWER)], groq_calls))
    finally:
        ai_manager.manual_text = saved
    assert groq_calls[0].startswith("OWNER'S MANUAL")
    assert "replace every 7,500 km" in groq_calls[0]
    assert "urgency_score" in groq_calls[0]  # the normal prompt follows the manual


def one_page_pdf(text: str) -> bytes:
    """A minimal valid PDF with one line of text (no extra libraries needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def test_manual_text_is_extracted_from_pdf():
    import os, tempfile
    path = os.path.join(tempfile.mkdtemp(), "manual.pdf")
    with open(path, "wb") as f:
        f.write(one_page_pdf("Engine oil every 10000 km or 12 months"))
    assert "Engine oil every 10000 km" in ai_manager.manual_text(path)


def test_manual_text_of_unreadable_file_is_empty():
    import os, tempfile
    path = os.path.join(tempfile.mkdtemp(), "broken.pdf")
    with open(path, "w") as f:
        f.write("this is not a pdf")
    assert ai_manager.manual_text(path) == ""


# --- Keeping the backup request inside Groq's free-tier limit (fixes 413) ----------

MANUAL = "\n".join([
    "SCHEDULED MAINTENANCE",
    "Item | Interval",
    "Engine oil and filter | 7,500 km or 6 months",
    "Brake pads | inspect every 15,000 km",
    "Brake fluid | replace every 30,000 km or 24 months",
    "Audio system | see chapter 4",
    "Seat adjustment | see chapter 2",
])


def test_excerpt_keeps_only_lines_about_the_part():
    excerpt = ai_manager.manual_excerpt(MANUAL, "Brake Pads")
    assert "Brake pads | inspect every 15,000 km" in excerpt
    assert "Seat adjustment" not in excerpt


def test_excerpt_finds_parts_under_the_manuals_own_names():
    # US-style manuals say "tire" and "air cleaner", not "tyre" and "air filter".
    manual = "\n".join(["Seat adjustment | see chapter 2", "",
                        "Tire rotation | every 13,000 km or 12 months", "", "Audio | chapter 4", "",
                        "Air cleaner filter | replace every 39,000 km", "", "Radio | chapter 5"])
    assert "Tire rotation" in ai_manager.manual_excerpt(manual, "Tyres")
    assert "Air cleaner filter" in ai_manager.manual_excerpt(manual, "Engine Air Filter")
    assert "Seat adjustment" not in ai_manager.manual_excerpt(manual, "Tyres")


def test_excerpt_falls_back_to_start_of_manual_when_part_not_mentioned():
    assert ai_manager.manual_excerpt(MANUAL, "Timing Belt").startswith("SCHEDULED MAINTENANCE")


def test_excerpt_is_capped():
    huge = "\n".join(f"Brake pads note {i}" for i in range(50_000))
    assert len(ai_manager.manual_excerpt(huge, "Brake Pads")) <= ai_manager.MAX_MANUAL_CHARS


def test_backup_request_fits_free_tier_even_with_a_huge_manual():
    groq_calls, saved = [], ai_manager.manual_text
    ai_manager.manual_text = lambda path: "\n".join(f"Engine oil line {i}" for i in range(100_000))
    try:
        ai_manager.process(RECORD, manual_path="manuals/kia_forte.pdf",
                           client=fake_client([ConnectionError("503 UNAVAILABLE")], []),
                           backup_client=fake_groq([json.dumps(GROQ_ANSWER)], groq_calls))
    finally:
        ai_manager.manual_text = saved
    approx_prompt_tokens = len(groq_calls[0]) / 4  # ~4 characters per token
    assert approx_prompt_tokens + ai_manager.GROQ_MAX_ANSWER_TOKENS < 8000


def test_waits_before_retrying_when_both_fail():
    waits, saved = [], ai_manager.time
    ai_manager.time = SimpleNamespace(sleep=waits.append)
    try:
        ai_manager.process(RECORD, client=fake_client([ConnectionError("503")], []),
                           backup_client=fake_groq([ConnectionError("413")], []), max_attempts=2)
    finally:
        ai_manager.time = saved
    assert waits == [ai_manager.RETRY_DELAY_SECONDS]  # one wait, between the two attempts