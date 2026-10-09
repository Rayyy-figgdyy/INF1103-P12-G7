"""
io_manager.py -- Input layer. All boundaries between system and user.

Framework brief, section 2.1:
  - all print() and input() calls in the codebase live here and nowhere else
  - validate type, range and required fields: reject and re-prompt on bad
    data, never crash
  - return clean, typed values/dicts -- no raw strings leave this file
  - display functions (display_record, display_list, display_result) live here

io_manager never stores data, calls the AI or applies business rules.
Anything it needs from storage (e.g. plates already taken, the last
odometer reading) is passed in by main.py.
"""
from __future__ import annotations

import datetime
import re

MENU_LOGGED_OUT = """
==== Car Maintenance & Service Risk Tracker ====
Not logged in
1) Login
2) Register new profile
3) Exit
"""

MENU_LOGGED_IN = """
==== Car Maintenance & Service Risk Tracker ====
Logged in: {name} | {plate} | {car_model}
--- Maintenance ---
1) Check a part (AI urgency assessment)
2) View my maintenance history
3) View my urgent / overdue items
--- Account ---
4) View my profile
5) Update my profile
6) Delete my profile
7) Logout
8) Exit
"""

# Singapore plate: 1-3 letters, 1-4 digits, 1 checksum letter (e.g. SBA1234A).
LICENSE_PLATE_PATTERN = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]$")
MAX_ODOMETER_KM = 2_000_000

# How dates are typed and shown. Dates are still SAVED as YYYY-MM-DD.
DATE_FORMAT = "%d/%m/%Y"
DATE_HINT = "DD/MM/YYYY"


# ======================================================================
# Generic validated prompts (private helpers)
# ======================================================================

def _prompt_text(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print(f"  -> {label} cannot be empty. Please try again.")


def _prompt_km(label: str) -> int:
    while True:
        raw = input(f"{label}: ").strip().replace(",", "")
        if not raw.isdigit():
            print(f"  -> {label} must be a whole number of km, e.g. 45200. Please try again.")
            continue
        value = int(raw)
        if value > MAX_ODOMETER_KM:
            print(f"  -> {label} looks too large (max {MAX_ODOMETER_KM:,} km). Please try again.")
            continue
        return value


def _parse_date(raw: str) -> datetime.date | None:
    """Read a date typed as DD/MM/YYYY (DD-MM-YYYY also works)."""
    try:
        return datetime.datetime.strptime(raw.replace("-", "/"), DATE_FORMAT).date()
    except ValueError:
        return None


def _show_date(iso_date) -> str:
    """Turn a saved YYYY-MM-DD date into DD/MM/YYYY for display."""
    try:
        return datetime.date.fromisoformat(str(iso_date)).strftime(DATE_FORMAT)
    except ValueError:
        return str(iso_date)


def _prompt_past_date(label: str) -> str:
    while True:
        parsed = _parse_date(input(f"{label} ({DATE_HINT}): ").strip())
        if parsed is None:
            print(f"  -> {label} must be a real date in {DATE_HINT} format. Please try again.")
        elif parsed > datetime.date.today():
            print(f"  -> {label} cannot be in the future. Please try again.")
        else:
            return parsed.isoformat()


def _normalise_plate(raw: str) -> str:
    """'sba 1234-a' -> 'SBA1234A', so lookups ignore case, spaces and dashes."""
    return re.sub(r"[\s\-]", "", raw).upper()


def _plate_error(plate: str, taken_plates: set[str]) -> str | None:
    if not plate:
        return "License plate cannot be empty."
    if not LICENSE_PLATE_PATTERN.match(plate):
        return "License plate must look like SBA1234A (letters, digits, then a letter)."
    if plate in taken_plates:
        return f"{plate} is already registered."
    return None


def confirm_action(question: str) -> bool:
    """Yes/no question; re-prompts until it gets y or n."""
    while True:
        raw = input(f"{question} (y/n): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  -> Please answer y or n.")


# ======================================================================
# Menu
# ======================================================================

def prompt_main_menu(current_user: dict | None) -> str:
    if current_user is None:
        print(MENU_LOGGED_OUT)
    else:
        print(MENU_LOGGED_IN.format(name=current_user["name"],
                                    plate=current_user["license_plate"],
                                    car_model=current_user["car_model"]))
    return input("Choose an option: ").strip()


# ======================================================================
# Account: login + profile CRUD
# ======================================================================

def prompt_login() -> str:
    print("\n--- Login ---")
    while True:
        plate = _normalise_plate(input("License plate: "))
        if plate:
            return plate
        print("  -> License plate cannot be empty. Please try again.")


def prompt_identity(taken_plates: set[str]) -> dict:
    """Name and a new, unique license plate for registration."""
    print("\n--- Register new profile ---")
    name = _prompt_text("Name")
    while True:
        plate = _normalise_plate(input("License plate (e.g. SBA1234A): "))
        error = _plate_error(plate, taken_plates)
        if error is None:
            return {"name": name, "license_plate": plate}
        print(f"  -> {error} Please try again.")


def prompt_car_choice(car_models: list[str]) -> str:
    """Pick the user's car from the supported models (main.py passes the
    list in from data_manager)."""
    print("\nWhich car do you drive?")
    for number, model in enumerate(car_models, start=1):
        print(f"  {number}) {model}")
    while True:
        raw = input("Car number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(car_models):
            return car_models[int(raw) - 1]
        print(f"  -> Please enter a number from 1 to {len(car_models)}.")


def prompt_coe_expiry() -> str:
    """COE expiry date. Past dates are allowed: an expired COE is real
    information that logic_manager acts on."""
    while True:
        coe = _parse_date(input(f"COE expiry date ({DATE_HINT}): ").strip())
        if coe:
            return coe.isoformat()
        print(f"  -> COE expiry date must be a real date in {DATE_HINT} format. Please try again.")


def prompt_updated_profile(current: dict) -> dict:
    """Edit name and COE expiry. Enter keeps the current value. The license
    plate and car are the account's identity and can't be changed --
    delete and re-register for a different car."""
    print("\n--- Update profile (press Enter to keep the current value) ---")
    print(f"License plate: {current['license_plate']} (cannot be changed)")
    print(f"Car:           {current['car_model']} ({current['car_condition']}) (cannot be changed)")
    updated = dict(current)

    raw = input(f"Name [{current['name']}]: ").strip()
    if raw:
        updated["name"] = raw

    while True:
        raw = input(f"COE expiry date ({DATE_HINT}) [{_show_date(current['coe_expiry_date'])}]: ").strip()
        if not raw:
            break
        coe = _parse_date(raw)
        if coe:
            updated["coe_expiry_date"] = coe.isoformat()
            break
        print(f"  -> Use {DATE_HINT}, or press Enter to keep.")

    return updated


def display_user(user: dict) -> None:
    print("\n--- Profile ---")
    print(f"Name:            {user.get('name')}")
    print(f"License plate:   {user.get('license_plate')}")
    print(f"Car model:       {user.get('car_model')}")
    print(f"Car condition:   {user.get('car_condition')}")
    print(f"COE expiry date: {_show_date(user.get('coe_expiry_date'))}")


# ======================================================================
# Maintenance check input
# ======================================================================

def prompt_part_choice(parts: list[str]) -> str:
    """Pick a part from those in the stored manufacturer schedule."""
    print("\nWhich part are you checking?")
    for number, part in enumerate(parts, start=1):
        print(f"  {number:>2}) {part}")
    while True:
        raw = input("Part number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(parts):
            return parts[int(raw) - 1]
        print(f"  -> Please enter a number from 1 to {len(parts)}.")


def prompt_service_details(last_known_odometer: int | None) -> dict:
    """Details from the last workshop invoice for this part, plus today's
    odometer reading. Business rule 1 is enforced here:
      - the last service mileage can't exceed the current odometer
      - the odometer can't be lower than the last reading on file for this car
    """
    print("\nFrom your last workshop invoice for this part:")
    last_service_date = _prompt_past_date("Date of last service")
    last_service_mileage = _prompt_km("Odometer at last service (km)")

    if last_known_odometer is not None:
        print(f"(Last odometer reading on file for this car: {last_known_odometer:,} km)")
    minimum = max(last_service_mileage, last_known_odometer or 0)
    while True:
        current_odometer = _prompt_km("Current odometer reading (km)")
        if current_odometer < last_service_mileage:
            print(f"  -> Can't be lower than the odometer at last service ({last_service_mileage:,} km). Please try again.")
        elif current_odometer < minimum:
            print(f"  -> Can't be lower than the last reading on file ({minimum:,} km). Please try again.")
        else:
            break

    return {
        "last_service_date": last_service_date,
        "last_service_mileage": last_service_mileage,
        "current_odometer": current_odometer,
    }


# ======================================================================
# Display
# ======================================================================

def display_message(message: str) -> None:
    print(message)


def display_record(record: dict) -> None:
    urgency = record.get("urgency_score")
    print("\n--- Maintenance check ---")
    print(f"Part:               {record.get('part_name')}  ({record.get('car_model')}, {record.get('license_plate')})")
    print(f"Checked on:         {_show_date(record.get('check_date'))} at {record.get('current_odometer'):,} km")
    print(f"Last serviced:      {_show_date(record.get('last_service_date'))} at {record.get('last_service_mileage'):,} km")
    print(f"Schedule:           every {record.get('interval_km'):,} km or {record.get('interval_months')} months")
    print(f"Since last service: {record.get('km_since_service'):,} km / {record.get('days_since_service')} days")
    if record.get("is_overdue"):
        print(f"Overdue by:         {record.get('km_overdue'):,} km / {record.get('days_overdue')} days")
    urgency_text = f"{urgency}/10" if urgency is not None else "n/a"
    print(f"AI urgency:         {urgency_text} (confidence: {record.get('confidence')})")
    print(f"Answered by:        {record.get('ai_provider', 'n/a')}")
    print(f"AI summary:         {record.get('overdue_summary')}")
    print(f"Manual reference:   {record.get('manual_reference')}")
    print(f"OUTCOME:            {record.get('outcome')}")
    print(f"Service by:         {_show_date(record.get('recommended_service_date'))}")
    print(f"Risk score:         {record.get('risk_score')}/100")
    print(f"Advice:             {record.get('recommended_action')}")


def display_list(records: list[dict]) -> None:
    if not records:
        print("\nNo records to show.")
        return
    print(f"\n{'Checked':<12}{'Part':<20}{'Odometer':>10}  {'Urgency':<8}{'Service by':<12}Outcome")
    for r in records:
        urgency = r.get("urgency_score")
        print(f"{_show_date(r.get('check_date')):<12}"
              f"{str(r.get('part_name'))[:19]:<20}"
              f"{r.get('current_odometer', 0):>10,}  "
              f"{(str(urgency) if urgency is not None else '-'):<8}"
              f"{_show_date(r.get('recommended_service_date')):<12}"
              f"{r.get('outcome')}")


def display_result(record: dict) -> None:
    print("\n>>> Assessment saved.")
    display_record(record)