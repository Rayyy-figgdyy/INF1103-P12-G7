"""
logic_manager.py -- OUR BUSINESS RULES (the "Logic layer", or "domain brain").

(New to Python? See the "How to read Python" guide at the top of main.py.)

WHAT THIS FILE DOES
    After the AI has given its opinion (an urgency score from 1 to 10 and
    how confident it is), this file applies OUR rules to decide what the
    car owner should do. Our project brief (section 2.3) asks for:
      - evaluate(record): work out the facts (overdue? service by when?)
      - score(record):    a number from 0 to 100, used to sort the list
      - route(record):    the final decision, e.g. "URGENT" or "OK"
      - at least one rule that combines TWO things the AI said
        -> in route(): urgency score AND confidence

OUR BUSINESS RULES (from our Project Initial Details)
    Rule 1, input validation: the odometer can't go backwards. (This is
            checked as the user types, in io_manager.py.)
    Rule 2, manual guidebook: how often a part needs servicing comes from
            our stored schedule (data/cars.json). This file never makes it up.
    Rule 3, service date: we recommend a "service by" date, based on how
            important the part is, how much the owner drives, the
            odometer, and the AI's urgency score.
    Plus a Singapore rule about the COE (Certificate of Entitlement):
            an expired COE means the car can't legally be driven, and a
            COE ending soon means non-safety servicing can wait.

WHY THE MATHS IS DONE HERE, NOT BY THE AI
    Exact numbers (km driven, days passed) are simple arithmetic. An AI
    can make mistakes with numbers, so we never rely on it for them.

This file doesn't show anything on screen, call the AI or touch files.

Phase 2 of the project (object-oriented version): the rules become methods
of a class, and different kinds of record get their own sub-classes.
"""
import datetime    # Python's built-in tools for dates

# ======================================================================
# FIXED SETTINGS
# ======================================================================

# Parts where a failure is a SAFETY risk (the car might not stop safely).
# These get extra urgency in the rules below.
SAFETY_CRITICAL_PARTS = ("brake pads", "brake fluid")

DAYS_PER_MONTH = 30.44           # average days in a month (so 24 months = 731 days)
COE_DEFER_WINDOW_DAYS = 180      # "COE ends soon" = within 180 days (about 6 months)

# Outcomes that mean "this needs the owner's attention" (shown in menu option 3).
URGENT_PREFIXES = ("URGENT", "FLAGGED", "DUE SOON", "COE EXPIRED")


# ======================================================================
# SMALL HELPERS
# ======================================================================

def _to_date(value) -> datetime.date | None:
    """Turn saved text like '2025-09-03' into a real date that Python can
    do maths with. Gives back None if the value is missing or isn't a date."""
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _add_days(start: datetime.date, days: int) -> datetime.date:
    """The date that is `days` days after `start` (e.g. 1 Oct + 3 = 4 Oct)."""
    return start + datetime.timedelta(days=days)


def _recommend_service_date(check_date: datetime.date, is_overdue: bool,
                            km_left: int | None, days_left: int | None,
                            km_per_day: float, urgency: int | None,
                            safety_critical: bool) -> datetime.date:
    """BUSINESS RULE 3: by what date should this part be serviced?

    Part A -- when does the schedule say it's due?
        Already overdue  -> today.
        Otherwise, whichever comes FIRST:
            the date the time limit runs out, or
            the date the km limit runs out, at the owner's own driving pace.
            (E.g. 5,000 km left at 50 km a day = 100 days from now.)

    Part B -- bring it forward if the AI says it's urgent:
        AI urgency 8 to 10 -> within 3 days   (brakes: within 1 day)
        AI urgency 5 to 7  -> within 14 days  (brakes: within 7 days)
    """
    # ---- Part A: the schedule's due date ----
    if is_overdue:
        due = check_date
    else:
        possible_dates = []
        if days_left is not None:
            possible_dates.append(_add_days(check_date, days_left))
        if km_left is not None and km_per_day > 0:
            days_until_km_limit = int(km_left / km_per_day)
            possible_dates.append(_add_days(check_date, days_until_km_limit))
        if possible_dates:
            due = min(possible_dates)       # min = the earliest date
        else:
            due = check_date

    # ---- Part B: the AI's urgency can bring it forward ----
    if urgency is not None and urgency >= 8:
        days_allowed = 3
    elif urgency is not None and urgency >= 5:
        days_allowed = 14
    else:
        days_allowed = None                 # low urgency: keep the schedule's date

    if days_allowed is not None:
        if safety_critical:
            # Brakes get half the time. // divides and rounds down
            # (3 // 2 = 1, 14 // 2 = 7). max(1, ...) means at least 1 day.
            days_allowed = max(1, days_allowed // 2)
        due = min(due, _add_days(check_date, days_allowed))

    return due


# ======================================================================
# evaluate -- work out the facts about one check
# ======================================================================

def evaluate(record: dict) -> dict:
    """Work out the facts about one maintenance check.
    Gives back a NEW dictionary of results (the record itself isn't changed).

    THE OVERDUE RULE: a part is overdue when EITHER limit is reached:
        km driven since the service   >= the km limit,  OR
        days passed since the service >= the month limit (in days)
    That matches how car manuals word it: "every 10,000 km or 12 months,
    whichever comes first"."""

    # ---- The dates ----
    # "x or y" here means: use x, but if x is missing, use y instead.
    check_date = _to_date(record.get("check_date")) or datetime.date.today()
    last_service_date = _to_date(record.get("last_service_date")) or check_date

    # ---- The stored schedule (business rule 2) ----
    # "or 0" turns a missing value into 0, so the maths still works.
    interval_km = int(record.get("interval_km") or 0)
    interval_days = round((record.get("interval_months") or 0) * DAYS_PER_MONTH)

    # ---- How far, and how long, since the last service ----
    # max(0, ...) means it can never go below zero.
    odometer_now = int(record.get("current_odometer") or 0)
    odometer_at_service = int(record.get("last_service_mileage") or 0)
    km_since = max(0, odometer_now - odometer_at_service)
    days_since = max(0, (check_date - last_service_date).days)

    # ---- Is it overdue? (by distance, by time, or both) ----
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

    # ---- Is it a brake part? ----
    part = str(record.get("part_name", "")).strip().lower()
    safety_critical = part in SAFETY_CRITICAL_PARTS

    # ---- Days until the COE expires (a negative number = already expired) ----
    coe_date = _to_date(record.get("coe_expiry_date"))
    if coe_date is not None:
        coe_days_left = (coe_date - check_date).days
    else:
        coe_days_left = None

    # ---- Business rule 3: the "service by" date ----
    service_by = _recommend_service_date(check_date, is_overdue, km_left, days_left,
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
    """The risk score:
            AI urgency x 10            (so urgency 7 = 70 points)
          + 15 if the part is overdue
          + 10 if it's a brake part
          and never more than 100.
    It's 0 if the AI couldn't give an urgency score.
    The score only SORTS the urgent list; it doesn't change any decision."""
    urgency = record.get("urgency_score")
    if urgency is None:
        return 0

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
    """Decide the outcome for one check.
    The rules are checked in order, TOP TO BOTTOM, and the FIRST one that
    matches gives the answer (each `return` ends the function). So the
    most important rules come first."""
    urgency = record.get("urgency_score")
    confidence = record.get("confidence")

    # Rule 1. The AI couldn't answer -> a person has to check it.
    if record.get("ai_status") != "ok" or urgency is None:
        return "NEEDS MANUAL REVIEW - AI assessment unavailable"

    facts = evaluate(record)
    coe_days_left = facts["coe_days_left"]

    # Rule 2. Singapore: an expired COE means the car can't be driven at
    # all, so there's no point recommending servicing.
    if coe_days_left is not None and coe_days_left < 0:
        return "COE EXPIRED - Renew the COE or deregister before servicing"

    # Rules 3 and 4. OUR MULTI-CONDITION RULE (required by the brief):
    # it combines TWO things the AI told us, the urgency AND the confidence.
    # The same high score leads to a different outcome depending on how
    # sure the AI is (i.e. how clearly the manual covers this part).
    if urgency >= 8 and confidence == "High":
        return "URGENT - Service immediately"
    if urgency >= 8:
        return "FLAGGED - High urgency but low AI confidence, verify with a mechanic"

    # Rule 5. Safety net: overdue brakes are ALWAYS urgent, whatever the AI said.
    if facts["is_safety_critical"] and facts["is_overdue"]:
        return "URGENT - Safety-critical part overdue"

    # Rule 6. Another multi-condition rule: the COE ends within 6 months
    # AND it's not a brake part AND the AI says it's not urgent
    # -> the servicing can probably wait.
    coe_ends_soon = coe_days_left is not None and coe_days_left <= COE_DEFER_WINDOW_DAYS
    if coe_ends_soon and not facts["is_safety_critical"] and urgency <= 4:
        return "DEFER - COE expires soon, non-critical service can wait"

    # Rules 7 to 9. Everything else.
    if facts["is_overdue"]:
        return "DUE SOON - Overdue by manufacturer schedule"
    if urgency <= 3:
        return "OK - Not urgent"
    return "MONITOR - Service approaching"


def is_urgent(record: dict) -> bool:
    """True if this check's outcome needs the owner's attention
    (its outcome starts with URGENT, FLAGGED, DUE SOON or COE EXPIRED)."""
    outcome = str(record.get("outcome", ""))
    return outcome.startswith(URGENT_PREFIXES)