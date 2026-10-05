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
  service_schedule.json     stored manufacturer service schedule (reference data,
                            committed to Git; proposal business rule #2)

No business rules and no terminal I/O live here -- storage only.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("data_manager")

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATA_FILE = os.path.join(_DATA_DIR, "maintenance_records.json")
USERS_FILE = os.path.join(_DATA_DIR, "users.json")
SCHEDULE_FILE = os.path.join(_DATA_DIR, "service_schedule.json")

# Built-in copy of the default schedule, used only if service_schedule.json
# is missing or corrupt, so the app keeps working instead of crashing.
FALLBACK_SCHEDULE: dict = {
    "default": {
        "Engine Oil": {"km": 10000, "months": 6},
        "Air Filter": {"km": 20000, "months": 12},
        "Brake Pads": {"km": 40000, "months": 24},
        "Brake Fluid": {"km": 40000, "months": 24},
        "Spark Plugs": {"km": 30000, "months": 24},
        "Tyres": {"km": 50000, "months": 48},
        "Coolant": {"km": 60000, "months": 48},
        "Battery": {"km": 60000, "months": 36},
        "Timing Belt": {"km": 100000, "months": 60},
    },
    "models": {},
}


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


# --- Stored manufacturer service schedule ---------------------------------

def _is_valid_interval(entry) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("km"), int) and entry["km"] > 0
        and isinstance(entry.get("months"), int) and entry["months"] > 0
    )


def load_schedule() -> dict:
    """Return the stored schedule, or the built-in fallback if the file is
    missing or malformed."""
    data = _read_json(SCHEDULE_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("default"), dict):
        if data is not None:
            logger.error("%s has no valid 'default' section -- using built-in schedule.", SCHEDULE_FILE)
        return FALLBACK_SCHEDULE
    if not isinstance(data.get("models"), dict):
        data["models"] = {}
    return data


def _schedule_for_model(car_model: str) -> dict:
    """Merge the default schedule with the most specific model override."""
    schedule = load_schedule()
    merged = {p: e for p, e in schedule["default"].items() if _is_valid_interval(e)}

    model_lower = (car_model or "").lower()
    matching_keys = [k for k in schedule["models"] if k.lower() in model_lower]
    if matching_keys:
        best = max(matching_keys, key=len)
        overrides = schedule["models"][best]
        if isinstance(overrides, dict):
            merged.update({p: e for p, e in overrides.items() if _is_valid_interval(e)})
    return merged


def list_parts(car_model: str) -> list[str]:
    """Parts that have a stored service interval for this car model."""
    return sorted(_schedule_for_model(car_model))


def get_service_interval(car_model: str, part_name: str) -> dict | None:
    """{'interval_km', 'interval_months'} for this model/part, or None."""
    entry = _schedule_for_model(car_model).get(part_name)
    if entry is None:
        return None
    return {"interval_km": entry["km"], "interval_months": entry["months"]}
