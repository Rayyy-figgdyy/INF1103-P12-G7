"""
data_manager.py -- the Data layer: saves and loads everything the app remembers.

Three JSON files in data/:
    maintenance_records.json   every finished maintenance check
    users.json                 user profiles, one per license plate
    cars.json                  the 4 supported cars and their service schedules
Pipeline: io -> ai -> logic -> data. Only main.py calls this file; no other manager
reads or writes these data files.
Only saving, loading and lookups here: no business decisions and no print().
See explanations/data_manager_explained.txt for a full walkthrough.
"""
import json
import logging
import os

logger = logging.getLogger("data_manager")

# Paths are built from this file's location, so the app finds data/ whatever folder
# it is started from (including inside Docker).
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "maintenance_records.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
CARS_FILE = os.path.join(DATA_DIR, "cars.json")

VALID_CONDITIONS = ("Brand New", "Used")

# A new check is a "repeat" when ALL five inputs match a saved check. main.py then shows
# the saved answer instead of calling the AI again: same inputs give the same result,
# with no extra API usage. check_date is not compared, so a repeat on another day counts.
REPEAT_FIELDS = ("license_plate", "part_name", "last_service_date",
                 "last_service_mileage", "current_odometer")


# ======================================================================
# READING AND WRITING FILES SAFELY
# Constraint C4: all data lives in flat JSON files (no database). These three helpers
# are the only code that opens a file, and none of them ever lets an error crash the app.
# ======================================================================

def read_json(path: str):
    """Read a JSON file and return its contents.
    Returns None if the file is missing, empty or damaged (problems are logged)."""
    if not os.path.exists(path):
        return None                     # no file yet: normal on first run
    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read().strip()
        if not content:
            return None
        return json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        # OSError = can't open the file; JSONDecodeError = the text isn't valid JSON.
        # Log it and carry on as if there were no data, instead of crashing.
        logger.error("Could not read %s (missing permission or corrupt JSON): %s", path, error)
        return None


def read_list(path: str) -> list[dict]:
    """Read a file that should hold a list (checks or users).
    Returns that list, or [] if the file is missing, damaged or not a list."""
    data = read_json(path)
    if isinstance(data, list):
        return data
    if data is not None:                # None was already logged by read_json
        logger.error("%s does not contain a list -- ignoring its contents.", path)
    return []


def write_list(path: str, items: list[dict]) -> bool:
    """Overwrite a file with the whole list, creating data/ if needed.
    Returns True on success, False if writing failed (the error is logged)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(items, file, indent=2)
        return True
    except OSError as error:
        logger.error("Could not write %s: %s", path, error)
        return False


# ======================================================================
# MAINTENANCE CHECKS: save, load and query (data/maintenance_records.json)
# ======================================================================

def load() -> list[dict]:
    """Return every saved maintenance check, oldest first ([] if none yet)."""
    return read_list(DATA_FILE)


def save(record: dict) -> bool:
    """Append one finished check to the file (reads all, adds one, rewrites all).
    Returns True if it was saved."""
    records = load()
    records.append(record)
    return write_list(DATA_FILE, records)


def query(filter_fn) -> list[dict]:
    """Return the saved checks for which filter_fn(check) is True, in saved order.
    Example: query(lambda r: r["part_name"] == "Coolant")"""
    matching = []
    for record in load():
        if filter_fn(record):
            matching.append(record)
    return matching


def same_inputs(record_a: dict, record_b: dict) -> bool:
    """Return True if the two checks match on every field in REPEAT_FIELDS."""
    for field in REPEAT_FIELDS:
        if record_a.get(field) != record_b.get(field):
            return False
    return True


def find_repeat(record: dict) -> dict | None:
    """Return the most recent saved check with the same inputs as record, or None."""
    matches = query(lambda saved: same_inputs(saved, record))
    if matches:
        return matches[-1]              # checks are appended, so the last match is newest
    return None


def get_last_odometer(records: list[dict], license_plate: str) -> int | None:
    """Return the highest odometer reading saved for this car, or None if there is none.
    Readings from every part count, since the odometer belongs to the whole car."""
    readings = []
    for record in records:
        odometer = record.get("current_odometer")
        # Skip readings that aren't whole numbers (e.g. hand-edited as "45000" text).
        if record.get("license_plate") == license_plate and isinstance(odometer, int):
            readings.append(odometer)
    if readings:
        return max(readings)
    return None


def delete_records_for_plate(license_plate: str) -> int:
    """Delete every saved check for one car. Returns how many were deleted.
    main.py calls this right after delete_user, so a deleted profile leaves no checks."""
    records = load()
    remaining = []
    for record in records:
        if record.get("license_plate") != license_plate:
            remaining.append(record)
    removed_count = len(records) - len(remaining)
    if removed_count > 0 and not write_list(DATA_FILE, remaining):
        return 0                        # the write failed, so nothing was really deleted
    return removed_count


# ======================================================================
# USER PROFILES: Create, Read, Update, Delete (data/users.json)
# The license plate is the unique key: one profile per plate.
# ======================================================================

def load_users() -> list[dict]:
    """Return every user profile ([] if none yet)."""
    return read_list(USERS_FILE)


def find_user_by_plate(license_plate: str) -> dict | None:
    """Return the profile with this license plate, or None."""
    for user in load_users():
        if user.get("license_plate") == license_plate:
            return user
    return None


def save_user(user: dict) -> bool:
    """Create a profile, or replace the existing one with the same plate.
    Returns True if it was saved."""
    users_to_keep = []
    for existing in load_users():
        if existing.get("license_plate") != user["license_plate"]:
            users_to_keep.append(existing)
    users_to_keep.append(user)          # an old copy with this plate was left out above
    return write_list(USERS_FILE, users_to_keep)


def delete_user(license_plate: str) -> bool:
    """Delete the profile with this plate (its checks are removed separately).
    Returns False if no such profile exists or saving failed."""
    users = load_users()
    remaining = []
    for user in users:
        if user.get("license_plate") != license_plate:
            remaining.append(user)
    if len(remaining) == len(users):
        return False                    # plate not found
    return write_list(USERS_FILE, remaining)


# ======================================================================
# THE CAR LIST (data/cars.json)
# Business rule 2: service intervals come ONLY from the stored manufacturer schedule
# here, never from the user or the AI. Entries are checked on load so a typo in
# cars.json can't crash the app.
# ======================================================================

def is_valid_interval(entry) -> bool:
    """Return True if a schedule line looks like {"km": 10000, "months": 12},
    with both values whole numbers above zero."""
    return (isinstance(entry, dict)
            and isinstance(entry.get("km"), int) and entry["km"] > 0
            and isinstance(entry.get("months"), int) and entry["months"] > 0)


def is_valid_car(car) -> bool:
    """Return True if a car entry has a non-blank model, a condition of Brand New or
    Used, a manual path (text) and a schedule (a dictionary)."""
    return (isinstance(car, dict)
            and isinstance(car.get("model"), str) and car["model"].strip() != ""
            and car.get("condition") in VALID_CONDITIONS
            and isinstance(car.get("manual"), str)
            and isinstance(car.get("schedule"), dict))


def load_cars() -> list[dict]:
    """Return every valid car from cars.json, in file order, with only valid schedule
    lines kept. Returns [] if the file is missing, damaged or has no "cars" list."""
    data = read_json(CARS_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("cars"), list):
        if data is not None:
            logger.error("%s has no 'cars' list.", CARS_FILE)
        return []

    cars = []
    for car in data["cars"]:
        # An invalid car is skipped and logged; the other cars still load.
        if not is_valid_car(car):
            logger.error("Skipping invalid car entry in %s: %r", CARS_FILE, car)
            continue

        # An invalid schedule line is dropped silently (not logged), so that part
        # just won't be offered for this car.
        good_schedule = {}
        for part, interval in car["schedule"].items():
            if is_valid_interval(interval):
                good_schedule[part] = interval

        clean_car = dict(car)           # a copy, so the loaded data isn't changed
        clean_car["schedule"] = good_schedule
        cars.append(clean_car)
    return cars


def list_car_models() -> list[str]:
    """Return the car model names to choose from at registration, in file order."""
    names = []
    for car in load_cars():
        names.append(car["model"])
    return names


def find_car(car_model: str) -> dict | None:
    """Return the cars.json entry with this exact model name, or None."""
    for car in load_cars():
        if car["model"] == car_model:
            return car
    return None


def list_parts(car_model: str) -> list[str]:
    """Return the part names in this car's schedule, A-Z ([] if the car is unknown)."""
    car = find_car(car_model)
    if car is None:
        return []
    return sorted(car["schedule"])


def get_service_interval(car_model: str, part_name: str) -> dict | None:
    """Return e.g. {"interval_km": 10000, "interval_months": 12} for this car and part,
    or None if either is unknown."""
    car = find_car(car_model)
    if car is None or part_name not in car["schedule"]:
        return None
    interval = car["schedule"][part_name]
    return {"interval_km": interval["km"], "interval_months": interval["months"]}


def get_manual_path(car_model: str) -> str | None:
    """Return the full path to this car's manual PDF, or None if the car is unknown
    or the file isn't there (a missing file is logged)."""
    car = find_car(car_model)
    if car is None:
        return None
    path = os.path.join(PROJECT_DIR, car["manual"])
    if not os.path.isfile(path):
        logger.error("Manual for %s not found at %s", car_model, path)
        return None
    return path
