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
import os
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
CAR_CONDITIONS = {"1": "Brand New", "2": "Used"}
_CONDITION_WORDS = {"brand new": "Brand New", "new": "Brand New", "used": "Used"}
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MAX_ODOMETER_KM = 2_000_000


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
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _prompt_past_date(label: str) -> str:
    while True:
        parsed = _parse_date(input(f"{label} (YYYY-MM-DD): ").strip())
        if parsed is None:
            print(f"  -> {label} must be a real date in YYYY-MM-DD format. Please try again.")
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


def _parse_condition(raw: str) -> str | None:
    raw = raw.strip()
    return CAR_CONDITIONS.get(raw) or _CONDITION_WORDS.get(raw.lower())


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


def prompt_car_model_source() -> str:
    """'text' or 'photo'."""
    while True:
        raw = input("Enter car model by  1) typing it  2) photo: ").strip()
        if raw == "1":
            return "text"
        if raw == "2":
            return "photo"
        print("  -> Please enter 1 or 2.")


def prompt_car_model_text() -> str:
    return _prompt_text("Car model (e.g. Toyota Corolla 2020)")


def prompt_photo_path() -> str:
    """Path to an existing image file. Accepts paths dragged into the
    terminal (quoted, or with escaped spaces)."""
    while True:
        raw = input("Path to car photo (e.g. photos/car7.jpg): ").strip().strip("'\"")
        path = os.path.expanduser(raw.replace("\\ ", " "))
        if not path:
            print("  -> Path cannot be empty. Please try again.")
        elif not os.path.isfile(path):
            print(f"  -> No file found at '{path}'. Please try again.")
        elif not path.lower().endswith(PHOTO_EXTENSIONS):
            print(f"  -> Photo must be one of: {', '.join(PHOTO_EXTENSIONS)}. Please try again.")
        else:
            return path


def confirm_identified_car(car: dict) -> bool:
    print("\nAI identified this car as:")
    print(f"  {car['make']} {car['model']} ({car['year_range']}) -- confidence: {car['confidence']}")
    return confirm_action("Use this as your car model?")


def prompt_car_details() -> dict:
    """Car condition and COE expiry date."""
    while True:
        condition = _parse_condition(input("Car condition  1) Brand New  2) Used: "))
        if condition:
            break
        print("  -> Please enter 1 (Brand New) or 2 (Used).")
    while True:
        # Past dates are allowed: an expired COE is real information.
        coe = _parse_date(input("COE expiry date (YYYY-MM-DD): ").strip())
        if coe:
            break
        print("  -> COE expiry date must be a real date in YYYY-MM-DD format. Please try again.")
    return {"car_condition": condition, "coe_expiry_date": coe.isoformat()}


def prompt_updated_profile(current: dict) -> dict:
    """Edit name, car model, condition and COE expiry. Enter keeps the
    current value. The license plate is the account's identity and can't
    be changed -- delete and re-register to use a different plate."""
    print("\n--- Update profile (press Enter to keep the current value) ---")
    print(f"License plate: {current['license_plate']} (cannot be changed)")
    updated = dict(current)

    raw = input(f"Name [{current['name']}]: ").strip()
    if raw:
        updated["name"] = raw

    raw = input(f"Car model [{current['car_model']}]: ").strip()
    if raw:
        updated["car_model"] = raw

    while True:
        raw = input(f"Car condition  1) Brand New  2) Used [{current['car_condition']}]: ").strip()
        if not raw:
            break
        condition = _parse_condition(raw)
        if condition:
            updated["car_condition"] = condition
            break
        print("  -> Enter 1 or 2, or press Enter to keep.")

    while True:
        raw = input(f"COE expiry date [{current['coe_expiry_date']}]: ").strip()
        if not raw:
            break
        coe = _parse_date(raw)
        if coe:
            updated["coe_expiry_date"] = coe.isoformat()
            break
        print("  -> Use YYYY-MM-DD, or press Enter to keep.")

    return updated


def display_user(user: dict) -> None:
    print("\n--- Profile ---")
    print(f"Name:            {user.get('name')}")
    print(f"License plate:   {user.get('license_plate')}")
    print(f"Car model:       {user.get('car_model')}")
    print(f"Car condition:   {user.get('car_condition')}")
    print(f"COE expiry date: {user.get('coe_expiry_date')}")


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
    print(f"Checked on:         {record.get('check_date')} at {record.get('current_odometer'):,} km")
    print(f"Last serviced:      {record.get('last_service_date')} at {record.get('last_service_mileage'):,} km")
    print(f"Schedule:           every {record.get('interval_km'):,} km or {record.get('interval_months')} months")
    print(f"Since last service: {record.get('km_since_service'):,} km / {record.get('days_since_service')} days")
    if record.get("is_overdue"):
        print(f"Overdue by:         {record.get('km_overdue'):,} km / {record.get('days_overdue')} days")
    urgency_text = f"{urgency}/10" if urgency is not None else "n/a"
    print(f"AI urgency:         {urgency_text} (confidence: {record.get('confidence')})")
    print(f"AI summary:         {record.get('overdue_summary')}")
    print(f"OUTCOME:            {record.get('outcome')}")
    print(f"Service by:         {record.get('recommended_service_date')}")
    print(f"Risk score:         {record.get('risk_score')}/100")
    print(f"Advice:             {record.get('recommended_action')}")


def display_list(records: list[dict]) -> None:
    if not records:
        print("\nNo records to show.")
        return
    print(f"\n{'Checked':<12}{'Part':<20}{'Odometer':>10}  {'Urgency':<8}{'Service by':<12}Outcome")
    for r in records:
        urgency = r.get("urgency_score")
        print(f"{str(r.get('check_date')):<12}"
              f"{str(r.get('part_name'))[:19]:<20}"
              f"{r.get('current_odometer', 0):>10,}  "
              f"{(str(urgency) if urgency is not None else '-'):<8}"
              f"{str(r.get('recommended_service_date')):<12}"
              f"{r.get('outcome')}")


def display_result(record: dict) -> None:
    print("\n>>> Assessment saved.")
    display_record(record)
