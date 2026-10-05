"""
Tests for logic_manager using hardcoded sample AI responses -- no live
API connection (framework brief 5.3). Run with:  python -m pytest
"""
import logic_manager

CHECK_DATE = "2026-10-01"


def sample_record(**overrides) -> dict:
    """An AI-enriched record as it arrives from ai_manager.process().
    Engine oil, schedule 10,000 km / 6 months (= 183 days), last serviced
    92 days before CHECK_DATE, 4,600 km ago (50 km per day)."""
    record = {
        "license_plate": "SBA1234A",
        "car_model": "Toyota Corolla 2020",
        "car_condition": "Used",
        "coe_expiry_date": "2030-01-01",
        "part_name": "Engine Oil",
        "interval_km": 10_000,
        "interval_months": 6,
        "last_service_date": "2026-07-01",
        "last_service_mileage": 40_000,
        "current_odometer": 44_600,
        "check_date": CHECK_DATE,
        # hardcoded sample AI response fields:
        "urgency_score": 2,
        "confidence": "High",
        "overdue_summary": "Engine oil: 5,400 km remaining",
        "recommended_action": "No action needed yet.",
        "ai_status": "ok",
    }
    record.update(overrides)
    return record


# --- evaluate() -------------------------------------------------------------

def test_evaluate_not_overdue():
    d = logic_manager.evaluate(sample_record())
    assert d["km_since_service"] == 4_600
    assert d["days_since_service"] == 92
    assert d["is_overdue"] is False
    assert d["km_overdue"] == 0 and d["days_overdue"] == 0
    assert d["km_per_day"] == 50.0


def test_evaluate_overdue_by_distance():
    d = logic_manager.evaluate(sample_record(current_odometer=50_650))  # 10,650 km since service
    assert d["is_overdue"] is True
    assert d["km_overdue"] == 650


def test_evaluate_overdue_by_time_only():
    # 1 year since service but very little driving: overdue by time, not distance.
    d = logic_manager.evaluate(sample_record(last_service_date="2025-10-01", current_odometer=41_000))
    assert d["is_overdue"] is True
    assert d["km_overdue"] == 0
    assert d["days_overdue"] == 365 - 183


def test_evaluate_does_not_modify_input():
    record = sample_record()
    before = dict(record)
    logic_manager.evaluate(record)
    assert record == before


def test_evaluate_flags_safety_critical_parts():
    assert logic_manager.evaluate(sample_record(part_name="Brake Pads"))["is_safety_critical"] is True
    assert logic_manager.evaluate(sample_record(part_name="Air Filter"))["is_safety_critical"] is False


# --- Business rule 3: recommended service date -------------------------------

def test_service_date_from_schedule_when_low_urgency():
    # Due by time in 91 days (2026-12-31); by distance at 50 km/day in 108 days.
    # The earlier one wins; urgency 2 doesn't pull it forward.
    d = logic_manager.evaluate(sample_record())
    assert d["recommended_service_date"] == "2026-12-31"


def test_service_date_today_when_overdue():
    d = logic_manager.evaluate(sample_record(current_odometer=55_000))
    assert d["recommended_service_date"] == CHECK_DATE


def test_high_ai_urgency_pulls_service_date_forward():
    d = logic_manager.evaluate(sample_record(urgency_score=9))
    assert d["recommended_service_date"] == "2026-10-04"  # within 3 days


def test_safety_critical_halves_the_urgency_window():
    d = logic_manager.evaluate(sample_record(urgency_score=6, part_name="Brake Pads"))
    assert d["recommended_service_date"] == "2026-10-08"  # 14 days halved to 7


# --- score() -------------------------------------------------------------------

def test_score_rises_when_overdue():
    on_time = logic_manager.score(sample_record(urgency_score=5))
    overdue = logic_manager.score(sample_record(urgency_score=5, current_odometer=55_000))
    assert overdue == on_time + 15


def test_score_is_capped_at_100():
    record = sample_record(urgency_score=10, part_name="Brake Pads", current_odometer=99_000)
    assert logic_manager.score(record) == 100


def test_score_zero_when_ai_unavailable():
    assert logic_manager.score(sample_record(urgency_score=None, ai_status="unavailable")) == 0


# --- route(): multi-condition rules -----------------------------------------------

def test_route_urgent_needs_high_score_and_high_confidence():
    assert logic_manager.route(sample_record(urgency_score=9, confidence="High")) == "URGENT - Service immediately"


def test_route_same_score_low_confidence_is_flagged_not_urgent():
    # Same urgency as above, only confidence differs -> different outcome.
    # Proves the rule depends on BOTH AI fields.
    assert logic_manager.route(sample_record(urgency_score=9, confidence="Low")).startswith("FLAGGED")


def test_route_safety_critical_overdue_is_urgent_even_at_moderate_score():
    record = sample_record(part_name="Brake Pads", urgency_score=6, current_odometer=85_000)
    assert logic_manager.route(record) == "URGENT - Safety-critical part overdue"


def test_route_coe_expired():
    assert logic_manager.route(sample_record(coe_expiry_date="2026-09-01")).startswith("COE EXPIRED")


def test_route_defer_when_coe_expiring_and_non_critical_and_low_urgency():
    record = sample_record(coe_expiry_date="2027-01-15", urgency_score=3)
    assert logic_manager.route(record).startswith("DEFER")


def test_route_no_defer_for_safety_critical_part_even_if_coe_expiring():
    record = sample_record(coe_expiry_date="2027-01-15", urgency_score=3, part_name="Brake Fluid")
    assert not logic_manager.route(record).startswith("DEFER")


def test_route_due_soon_when_overdue():
    assert logic_manager.route(sample_record(urgency_score=6, current_odometer=55_000)).startswith("DUE SOON")


def test_route_ok_when_low_urgency_and_on_schedule():
    assert logic_manager.route(sample_record(urgency_score=2)) == "OK - Not urgent"


def test_route_monitor_for_middle_urgency():
    assert logic_manager.route(sample_record(urgency_score=5)).startswith("MONITOR")


def test_route_manual_review_when_ai_unavailable():
    record = sample_record(urgency_score=None, confidence="Unknown", ai_status="unavailable")
    assert logic_manager.route(record).startswith("NEEDS MANUAL REVIEW")


def test_is_urgent():
    assert logic_manager.is_urgent({"outcome": "URGENT - Service immediately"}) is True
    assert logic_manager.is_urgent({"outcome": "OK - Not urgent"}) is False
    assert logic_manager.is_urgent({}) is False
