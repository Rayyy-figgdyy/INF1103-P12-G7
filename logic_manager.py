"""
logic_manager.py -- the business rules (Logic layer).

Takes one maintenance check that the AI has already scored and applies our rules:
  - evaluate(): the facts (overdue by km OR time, km/day, COE days left, service-by date)
  - score():    a 0-100 risk score, used only to sort the urgent list
  - route():    the final outcome text; includes the urgency + confidence rule
Rules held here: overdue = whichever limit comes first, brake pads and tyres are
safety-critical, the "service by" date (business rule 3) and the Singapore COE rules.
All arithmetic is done here, not by the AI. No screen, AI or file access.
See explanations/logic_manager_explained.txt for a full walkthrough.
"""
import datetime    # Python's built-in tools for dates

# ======================================================================
# FIXED SETTINGS
# ======================================================================

# A failure of these parts is a safety risk (stopping and grip), so they get extra
# urgency below. Lower case so they match part names after .lower().
SAFETY_CRITICAL_PARTS = ("brake pads", "tyres")

DAYS_PER_MONTH = 30.44           # average days in a month (so 24 months = 731 days)
COE_DEFER_WINDOW_DAYS = 180      # "COE ends soon" = within 180 days (about 6 months)

# Outcomes that need the owner's attention: any outcome starting with one of these
# is listed in menu option 3.
URGENT_PREFIXES = ("URGENT", "FLAGGED", "DUE SOON", "COE EXPIRED", "NEEDS MANUAL REVIEW")


# ======================================================================
# SMALL HELPERS
# ======================================================================

def to_date(value) -> datetime.date | None:
    """Turn saved text like '2025-09-03' into a date.
    Returns None if the value is missing or isn't a valid date."""
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def add_days(start: datetime.date, days: int) -> datetime.date:
    """Return the date `days` days after `start`."""
    return start + datetime.timedelta(days=days)


def recommend_service_date(check_date: datetime.date, is_overdue: bool,
                            km_left: int | None, days_left: int | None,
                            km_per_day: float, urgency: int | None,
                            safety_critical: bool) -> datetime.date:
    """BUSINESS RULE 3: work out the "service by" date for one part.
    Starts from the schedule's due date, then brings it forward if the AI's
    urgency is high. Returns the recommended date."""
    # ---- Part A: the schedule's due date ----
    if is_overdue:
        due = check_date
    else:
        # Due when EITHER limit runs out: the time limit, or the km limit reached at
        # the owner's own driving pace. int() rounds down, so we never recommend late.
        possible_dates = []
        if days_left is not None:
            possible_dates.append(add_days(check_date, days_left))
        if km_left is not None and km_per_day > 0:
            days_until_km_limit = int(km_left / km_per_day)
            possible_dates.append(add_days(check_date, days_until_km_limit))
        if possible_dates:
            due = min(possible_dates)       # min = the earliest date
        else:
            due = check_date

    # ---- Part B: the AI's urgency can bring it forward ----
    # Urgency 8-10: within 3 days. Urgency 5-7: within 14 days. 1-4: keep Part A.
    if urgency is not None and urgency >= 8:
        days_allowed = 3
    elif urgency is not None and urgency >= 5:
        days_allowed = 14
    else:
        days_allowed = None                 # low urgency: keep the schedule's date

    if days_allowed is not None:
        if safety_critical:
            # Brake pads and tyres get half the time (3 -> 1 day, 14 -> 7 days),
            # never less than 1 day.
            days_allowed = max(1, days_allowed // 2)
        # min(): urgency can only bring the date forward, never push it later.
        due = min(due, add_days(check_date, days_allowed))

    return due


# ======================================================================
# evaluate -- work out the facts about one check
# ======================================================================

def evaluate(record: dict) -> dict:
    """Work out the facts about one check: km/days since service, overdue status,
    km per day, safety-critical, COE days left and the "service by" date.
    Returns a NEW dictionary of these facts; the record itself isn't changed."""

    # ---- The dates ----
    # Missing check date -> today; missing last-service date -> 0 days since service.
    check_date = to_date(record.get("check_date")) or datetime.date.today()
    last_service_date = to_date(record.get("last_service_date")) or check_date

    # ---- The stored schedule (business rule 2) ----
    # Intervals always come from our stored schedule (cars.json), never from the AI.
    interval_km = int(record.get("interval_km") or 0)
    interval_days = round((record.get("interval_months") or 0) * DAYS_PER_MONTH)

    # ---- How far, and how long, since the last service ----
    # max(0, ...) guards against bad data giving negative distances or days.
    odometer_now = int(record.get("current_odometer") or 0)
    odometer_at_service = int(record.get("last_service_mileage") or 0)
    km_since = max(0, odometer_now - odometer_at_service)
    days_since = max(0, (check_date - last_service_date).days)

    # ---- Is it overdue? (by distance, by time, or both) ----
    # Overdue when EITHER limit is reached ("10,000 km or 12 months, whichever comes
    # first", as manuals put it). A limit of 0 means the part has no such limit.
    overdue_by_km = interval_km > 0 and km_since >= interval_km
    overdue_by_time = interval_days > 0 and days_since >= interval_days
    is_overdue = overdue_by_km or overdue_by_time

    # ---- How much over, or how much is left ----
    if interval_km > 0:
        km_overdue = max(0, km_since - interval_km)
        km_left = max(0, interval_km - km_since)
    else:
        km_overdue = 0
        km_left = None                      # no km limit for this part
    if interval_days > 0:
        days_overdue = max(0, days_since - interval_days)
        days_left = max(0, interval_days - days_since)
    else:
        days_overdue = 0
        days_left = None                    # no time limit for this part

    # ---- How much the owner drives (km per day) ----
    if days_since > 0:
        km_per_day = km_since / days_since
    else:
        km_per_day = 0.0                    # serviced today: no pace to measure yet

    # ---- Is it a safety-critical part (brake pads or tyres)? ----
    part = str(record.get("part_name", "")).strip().lower()
    safety_critical = part in SAFETY_CRITICAL_PARTS

    # ---- Days until the COE expires (a negative number = already expired) ----
    coe_date = to_date(record.get("coe_expiry_date"))
    if coe_date is not None:
        coe_days_left = (coe_date - check_date).days
    else:
        coe_days_left = None

    # ---- Business rule 3: the "service by" date ----
    service_by = recommend_service_date(check_date, is_overdue, km_left, days_left,
                                         km_per_day, record.get("urgency_score"), safety_critical)

    return {
        "km_since_service": km_since,
        "days_since_service": days_since,
        "km_overdue": km_overdue,
        "days_overdue": days_overdue,
        "is_overdue": is_overdue,
        "is_safety_critical": safety_critical,
        "km_per_day": round(km_per_day, 1),          # 1 decimal place
        "coe_days_left": coe_days_left,
        "recommended_service_date": service_by.isoformat(),
    }


# ======================================================================
# score -- a number from 0 to 100, used to sort the urgent list
# ======================================================================

def score(record: dict) -> int:
    """Return the risk score (0-100) for one check.
    Used only to sort the urgent list, most serious first; it never changes
    a decision. Returns 0 if the AI gave no urgency score."""
    urgency = record.get("urgency_score")
    if urgency is None:
        return 0

    # Points: urgency x 10, +15 if overdue, +10 if safety-critical, capped at 100.
    facts = evaluate(record)
    total = urgency * 10
    if facts["is_overdue"]:
        total = total + 15
    if facts["is_safety_critical"]:
        total = total + 10
    return min(100, total)        # min(100, ...) caps it at 100


# ======================================================================
# route -- the final decision
# ======================================================================

def route(record: dict) -> str:
    """Decide the final outcome for one check and return it as text,
    e.g. "URGENT - Service immediately"."""
    # The rules are checked top to bottom and the FIRST match wins (each `return`
    # ends the function), so the most serious rules must come first.
    urgency = record.get("urgency_score")
    confidence = record.get("confidence")

    # Rule 1. No valid AI answer: none of the AI-based rules below can be trusted.
    if record.get("ai_status") != "ok" or urgency is None:
        return "NEEDS MANUAL REVIEW - AI assessment unavailable"

    facts = evaluate(record)
    coe_days_left = facts["coe_days_left"]

    # Rule 2. Singapore: an expired COE means the car can't legally be driven,
    # so servicing advice is pointless until the COE is renewed.
    if coe_days_left is not None and coe_days_left < 0:
        return "COE EXPIRED - Renew the COE or deregister before servicing"

    # Rules 3 and 4. MULTI-CONDITION RULE (required by the brief): combines the AI's
    # urgency AND its confidence. A high score is only acted on directly when the
    # AI is sure; otherwise a mechanic should confirm it.
    if urgency >= 8 and confidence == "High":
        return "URGENT - Service immediately"
    if urgency >= 8:
        return "FLAGGED - High urgency but low AI confidence, verify with a mechanic"

    # Rule 5. Safety net: overdue brake pads or tyres are ALWAYS urgent, even if the
    # AI's urgency is low.
    if facts["is_safety_critical"] and facts["is_overdue"]:
        return "URGENT - Safety-critical part overdue"

    # Rule 6. COE defer rule: COE ends within 180 days AND not a safety part AND
    # urgency 4 or less -> the service can wait (the car may soon be off the road).
    coe_ends_soon = coe_days_left is not None and coe_days_left <= COE_DEFER_WINDOW_DAYS
    if coe_ends_soon and not facts["is_safety_critical"] and urgency <= 4:
        return "DEFER - COE expires soon, non-critical service can wait"

    # Rules 7 to 9. Everything else: overdue -> DUE SOON, urgency 1-3 -> OK,
    # urgency 4-7 -> MONITOR.
    if facts["is_overdue"]:
        return "DUE SOON - Overdue by manufacturer schedule"
    if urgency <= 3:
        return "OK - Not urgent"
    return "MONITOR - Service approaching"


def is_urgent(record: dict) -> bool:
    """Return True if the saved outcome needs the owner's attention, i.e. it starts
    with one of URGENT_PREFIXES. main.py uses this for menu option 3."""
    outcome = str(record.get("outcome", ""))
    return outcome.startswith(URGENT_PREFIXES)
