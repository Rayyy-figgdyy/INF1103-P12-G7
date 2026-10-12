"""
Regression tests for io_manager's display functions: a damaged or
old-format record in the data file must never crash the history screen.
"""
import io_manager

DAMAGED_RECORDS = [
    {"part_name": "Tyres", "current_odometer": "52000", "urgency_score": None,
     "outcome": "NEEDS MANUAL REVIEW"},                         # odometer saved as text
    {"part_name": "Engine Oil", "outcome": "OK - Not urgent"},  # most fields missing
    {"part_name": "Coolant", "current_odometer": None, "check_date": "not a date"},
]


def test_history_list_survives_damaged_records():
    io_manager.display_list(DAMAGED_RECORDS)  # must not raise


def test_assessment_screen_survives_missing_fields():
    io_manager.display_record({"part_name": "Battery", "is_overdue": True})  # must not raise


def test_km_formatting():
    assert io_manager.km(49000) == "49,000"
    assert io_manager.km("52000") == "52,000"
    assert io_manager.km(None) == "-"
    assert io_manager.km("lots") == "lots"


def test_date_formatting():
    assert io_manager.show_date("2026-10-09") == "09/10/2026"
    assert io_manager.show_date(None) == "-"
    assert io_manager.show_date("not a date") == "not a date"