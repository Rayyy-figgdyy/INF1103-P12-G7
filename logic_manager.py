"""
logic_manager.py -- Logic layer. The domain brain.

Framework brief, section 2.3 -- acts on what the AI returned:
  - evaluate(record)  runs the business rules; returns a decision dict
  - score(record)     numeric score from AI output fields, for ranking/thresholds
  - route(record)     assigns the record to an outcome
  - at least one multi-condition rule using two or more AI response fields

Business rules (from Group 7's Project Initial Details):
  1. Input validation -- odometer can't go backwards and service mileage
     can't exceed the odometer. Enforced at input time by io_manager.
  2. Manual guidebook -- the service interval comes from the stored
     manufacturer schedule (data_manager supplies interval_km and
     interval_months on the record). This module never invents one.
  3. Service date recommendation -- from part importance, driving history
     (km per day since the last service), the current odometer and the
     AI urgency score.
Plus a Singapore-specific rule: an expired COE blocks servicing advice,
and a COE expiring soon lets non-critical servicing be deferred.

No terminal I/O, API calls or file access in this module.
"""
from __future__ import annotations

import datetime

SAFETY_CRITICAL_PARTS = {"brake pads", "brake fluid"}
DAYS_PER_MONTH = 30.44
COE_DEFER_WINDOW_DAYS = 180

# Highest urgency band first: (minimum AI urgency score, service within N days)
URGENCY_SERVICE_WINDOWS = ((8, 3), (5, 14))

# Outcomes that count as "needs attention" for the urgent view.
URGENT_PREFIXES = ("URGENT", "FLAGGED", "DUE SOON", "COE EXPIRED")


def _parse_date(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _is_safety_critical(part_name: str) -> bool:
    return (part_name or "").strip().lower() in SAFETY_CRITICAL_PARTS


def _recommend_service_date(check_date: datetime.date, is_overdue: bool,
                            km_remaining: int | None, days_remaining: int | None,
                            km_per_day: float, urgency_score: int | None,
                            safety_critical: bool) -> datetime.date:
    """Business rule 3. Starts from when the schedule says the part is due
    (by time, or by distance at the owner's own driving pace), then pulls
    the date earlier when the AI urgency score is high, and earlier still
    for safety-critical parts."""
    if is_overdue:
        due = check_date
    else:
        candidates = []
        if days_remaining is not None:
            candidates.append(check_date + datetime.timedelta(days=days_remaining))
        if km_remaining is not None and km_per_day > 0:
            candidates.append(check_date + datetime.timedelta(days=int(km_remaining / km_per_day)))
        due = min(candidates) if candidates else check_date

    if urgency_score is not None:
        for min_score, window_days in URGENCY_SERVICE_WINDOWS:
            if urgency_score >= min_score:
                if safety_critical:
                    window_days = max(1, window_days // 2)
                due = min(due, check_date + datetime.timedelta(days=window_days))
                break
    return due


def evaluate(record: dict) -> dict:
    """Run the deterministic business rules on one AI-enriched record and
    return the decision fields to merge into it. Does not modify `record`.

    Distance and time arithmetic is done here, not by the AI -- exact
    numbers should never depend on a language model."""
    check_date = _parse_date(record.get("check_date")) or datetime.date.today()
    last_service_date = _parse_date(record.get("last_service_date")) or check_date

    interval_km = int(record.get("interval_km") or 0)
    interval_days = round((record.get("interval_months") or 0) * DAYS_PER_MONTH)

    km_since = max(0, int(record.get("current_odometer") or 0) - int(record.get("last_service_mileage") or 0))
    days_since = max(0, (check_date - last_service_date).days)

    km_overdue = max(0, km_since - interval_km) if interval_km else 0
    days_overdue = max(0, days_since - interval_days) if interval_days else 0
    km_remaining = max(0, interval_km - km_since) if interval_km else None
    days_remaining = max(0, interval_days - days_since) if interval_days else None
    is_overdue = bool((interval_km and km_since >= interval_km) or (interval_days and days_since >= interval_days))

    safety_critical = _is_safety_critical(record.get("part_name", ""))
    km_per_day = km_since / days_since if days_since else 0.0

    coe_date = _parse_date(record.get("coe_expiry_date"))
    coe_days_left = (coe_date - check_date).days if coe_date else None

    recommended = _recommend_service_date(
        check_date, is_overdue, km_remaining, days_remaining, km_per_day,
        record.get("urgency_score"), safety_critical,
    )

    return {
        "km_since_service": km_since,
        "days_since_service": days_since,
        "km_overdue": km_overdue,
        "days_overdue": days_overdue,
        "is_overdue": is_overdue,
        "is_safety_critical": safety_critical,
        "km_per_day": round(km_per_day, 1),
        "coe_days_left": coe_days_left,
        "recommended_service_date": recommended.isoformat(),
    }


def score(record: dict) -> int:
    """Risk score 0-100 for ranking: the AI urgency score scaled to 10-100,
    plus 15 if overdue by schedule and 10 if safety-critical. 0 if the AI
    assessment is unavailable."""
    urgency_score = record.get("urgency_score")
    if urgency_score is None:
        return 0
    decision = evaluate(record)
    total = urgency_score * 10
    if decision["is_overdue"]:
        total += 15
    if decision["is_safety_critical"]:
        total += 10
    return min(100, total)


def route(record: dict) -> str:
    """Assign the record an outcome. Rules are checked in priority order."""
    urgency_score = record.get("urgency_score")
    confidence = record.get("confidence")

    if record.get("ai_status") != "ok" or urgency_score is None:
        return "NEEDS MANUAL REVIEW - AI assessment unavailable"

    d = evaluate(record)
    coe_days_left = d["coe_days_left"]

    if coe_days_left is not None and coe_days_left < 0:
        return "COE EXPIRED - Renew the COE or deregister before servicing"

    # Multi-condition rule on two AI response fields: urgency_score AND confidence.
    if urgency_score >= 8 and confidence == "High":
        return "URGENT - Service immediately"
    if urgency_score >= 8:
        return "FLAGGED - High urgency but low AI confidence, verify with a mechanic"

    if d["is_safety_critical"] and d["is_overdue"]:
        return "URGENT - Safety-critical part overdue"

    # Multi-condition rule: COE window AND part importance AND AI urgency.
    if (coe_days_left is not None and coe_days_left <= COE_DEFER_WINDOW_DAYS
            and not d["is_safety_critical"] and urgency_score <= 4):
        return "DEFER - COE expires soon, non-critical service can wait"

    if d["is_overdue"]:
        return "DUE SOON - Overdue by manufacturer schedule"
    if urgency_score <= 3:
        return "OK - Not urgent"
    return "MONITOR - Service approaching"


def is_urgent(record: dict) -> bool:
    """True if the record's outcome needs the owner's attention."""
    return str(record.get("outcome", "")).startswith(URGENT_PREFIXES)