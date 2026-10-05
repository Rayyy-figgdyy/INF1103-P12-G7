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

GOOD = {
    "urgency_score": 7,
    "confidence": "High",
    "overdue_summary": "Engine oil: 650 km overdue",
    "recommended_action": "Book an oil change this week.",
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
    bad = dict(GOOD)
    del bad["confidence"]
    assert ai_manager.validate_response(bad) is None


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


# --- identify_car_model (photo) --------------------------------------------------

def test_identify_car_model_uploads_photo_and_validates():
    calls = []
    reply = json.dumps({"make": "Toyota", "model": "Corolla Altis", "year_range": "2019-2023", "confidence": "High"})
    car = ai_manager.identify_car_model("photos/car.jpg", client=fake_client([reply], calls))
    assert car == {"make": "Toyota", "model": "Corolla Altis", "year_range": "2019-2023", "confidence": "High"}
    assert calls[0][0] == "<uploaded photos/car.jpg>"  # image sent before the prompt


def test_identify_car_model_returns_none_on_bad_reply():
    car = ai_manager.identify_car_model("photos/car.jpg", client=fake_client(['{"make": "Toyota"}'], []))
    assert car is None
