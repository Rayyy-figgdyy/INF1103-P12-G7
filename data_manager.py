"""
data_manager.py -- Data layer. The system's memory across runs.

Framework brief, section 2.4:
  - save(record)      append a processed record after logic_manager has evaluated it
  - load()            read all records on startup; [] if the file doesn't exist
  - query(filter_fn)  return the records matching a filter function
  - missing/corrupt files: log the error, return empty, continue -- never crash

Files (all flat JSON in data/):
  maintenance_records.json  AI-processed maintenance checks (written by the app)
  users.json                user/car profiles, keyed by license_plate (written by the app)
  cars.json                 the 4 supported cars: condition, manual PDF and
                            manufacturer service schedule (reference data,
                            committed to Git; proposal business rule #2)

No business rules and no terminal I/O live here -- storage only.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("data_manager")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(PROJECT_DIR, "data")
DATA_FILE = os.path.join(_DATA_DIR, "maintenance_records.json")
USERS_FILE = os.path.join(_DATA_DIR, "users.json")
CARS_FILE = os.path.join(_DATA_DIR, "cars.json")

VALID_CONDITIONS = ("Brand New", "Used")


# --- Generic JSON helpers ----------------------------------------------

def _read_json(path: str):
    """Return the parsed JSON in path, or None if the file is missing,
    empty, unreadable or corrupt (after logging). Never raises."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except OSError as exc:
        logger.error("Could not read %s: %s", path, exc)
        return None
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("%s is corrupt and could not be parsed: %s", path, exc)
        return None


def _load_json_list(path: str) -> list[dict]:
    data = _read_json(path)
    if data is None:
        return []
    if not isinstance(data, list):
        logger.error("%s does not contain a JSON list -- ignoring its contents.", path)
        return []
    return data


def _write_json_list(path: str, items: list[dict]) -> bool:
    """Overwrite path with items. Returns False (after logging) on failure."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
        return True
    except OSError as exc:
        logger.error("Could not write %s: %s", path, exc)
        return False


# --- Maintenance records ------------------------------------------------

def load() -> list[dict]:
    """Read and return all maintenance records."""
    return _load_json_list(DATA_FILE)


def save(record: dict) -> bool:
    """Append one processed record. Returns True on success."""
    records = load()
    records.append(record)
    return _write_json_list(DATA_FILE, records)


def query(filter_fn) -> list[dict]:
    """Return every stored record for which filter_fn(record) is True."""
    return [r for r in load() if filter_fn(r)]


def get_last_odometer(records: list[dict], license_plate: str) -> int | None:
    """Highest odometer reading on file for this car (any part), or None.
    An odometer belongs to the car, so it is tracked per license plate."""
    readings = [
        r.get("current_odometer", 0) for r in records
        if r.get("license_plate") == license_plate
    ]
    return max(readings) if readings else None


def delete_records_for_plate(license_plate: str) -> int:
    """Remove all maintenance records for a car. Returns how many were removed."""
    records = load()
    remaining = [r for r in records if r.get("license_plate") != license_plate]
    removed = len(records) - len(remaining)
    if removed and not _write_json_list(DATA_FILE, remaining):
        return 0
    return removed


# --- User profiles (CRUD) -------------------------------------------------

def load_users() -> list[dict]:
    """READ (all) user profiles."""
    return _load_json_list(USERS_FILE)


def find_user_by_plate(license_plate: str) -> dict | None:
    """READ (one): the profile with this license plate, or None."""
    for user in load_users():
        if user.get("license_plate") == license_plate:
            return user
    return None


def save_user(user: dict) -> bool:
    """CREATE or UPDATE: insert the profile, or replace the one with the
    same license plate. Returns True on success."""
    users = [u for u in load_users() if u.get("license_plate") != user["license_plate"]]
    users.append(user)
    return _write_json_list(USERS_FILE, users)


def delete_user(license_plate: str) -> bool:
    """DELETE: remove the profile. False if none matched or the write failed."""
    users = load_users()
    remaining = [u for u in users if u.get("license_plate") != license_plate]
    if len(remaining) == len(users):
        return False
    return _write_json_list(USERS_FILE, remaining)


# --- Car catalogue: the 4 supported cars --------------------------------

def _is_valid_interval(entry) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("km"), int) and entry["km"] > 0
        and isinstance(entry.get("months"), int) and entry["months"] > 0
    )


def _clean_car(entry) -> dict | None:
    """A catalogue entry with only its valid schedule lines, or None if the
    entry itself is unusable (logged)."""
    if not (isinstance(entry, dict)
            and isinstance(entry.get("model"), str) and entry["model"].strip()
            and entry.get("condition") in VALID_CONDITIONS
            and isinstance(entry.get("manual"), str)
            and isinstance(entry.get("schedule"), dict)):
        logger.error("Skipping invalid car entry in %s: %r", CARS_FILE, entry)
        return None
    schedule = {part: e for part, e in entry["schedule"].items() if _is_valid_interval(e)}
    return {**entry, "schedule": schedule}


def load_cars() -> list[dict]:
    """All valid cars in the catalogue, in file order. [] if the file is
    missing or corrupt -- never raises."""
    data = _read_json(CARS_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("cars"), list):
        if data is not None:
            logger.error("%s has no 'cars' list.", CARS_FILE)
        return []
    return [car for car in map(_clean_car, data["cars"]) if car is not None]


def list_car_models() -> list[str]:
    """Model names users can choose from when registering."""
    return [car["model"] for car in load_cars()]


def find_car(car_model: str) -> dict | None:
    """The catalogue entry for this model, or None."""
    for car in load_cars():
        if car["model"] == car_model:
            return car
    return None


def list_parts(car_model: str) -> list[str]:
    """Parts with a stored service interval for this car."""
    car = find_car(car_model)
    return sorted(car["schedule"]) if car else []


def get_service_interval(car_model: str, part_name: str) -> dict | None:
    """{'interval_km', 'interval_months'} for this car/part, or None."""
    car = find_car(car_model)
    entry = car["schedule"].get(part_name) if car else None
    if entry is None:
        return None
    return {"interval_km": entry["km"], "interval_months": entry["months"]}


def get_manual_path(car_model: str) -> str | None:
    """Full path to this car's manual PDF, or None (logged) if the car is
    unknown or the file isn't there."""
    car = find_car(car_model)
    if car is None:
        return None
    path = os.path.join(PROJECT_DIR, car["manual"])
    if not os.path.isfile(path):
        logger.error("Manual for %s not found at %s", car_model, path)
        return None
    return path