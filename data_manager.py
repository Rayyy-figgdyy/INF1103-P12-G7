"""
data_manager.py - The Data Storage Layer

This file manages reading and writing data to three JSON files inside the data/ folder:
    1. maintenance_records.json - History of all completed vehicle checks
    2. users.json                - User account profiles (one per license plate)
    3. cars.json                 - Supported car models and official maintenance schedules

Architecture Flow: io -> ai -> logic -> data
Only main.py calls this file directly.
This file ONLY handles saving, loading, and looking up data. It contains no business logic.
"""
import json
import logging
import os

logger = logging.getLogger("data_manager")

# Find project directory so file paths work everywhere (including inside Docker)
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "maintenance_records.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
CARS_FILE = os.path.join(DATA_DIR, "cars.json")

VALID_CONDITIONS = ("Brand New", "Used")

# Fields used to detect a duplicate check. If all 5 match a previous check,
# main.py reuses the saved answer instead of calling the AI API again.
REPEAT_FIELDS = ("license_plate", "part_name", "last_service_date",
                 "last_service_mileage", "current_odometer")


# ======================================================================
# FILE I/O HELPERS
# Safe functions for opening and saving JSON files without crashing the app.
# ======================================================================

def read_json(path: str):
    """Read a JSON file and return its data.
    Returns None if missing, empty, or corrupted (errors are logged quietly)."""
    if not os.path.exists(path):
        return None                     # Normal on the first run before data files exist
    try:
        with open(path, "r", encoding="utf-8") as file:
            content = file.read().strip()
        if not content:
            return None
        return json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        # Catch file access or JSON formatting errors so the app keeps running
        logger.error("Could not read %s (missing permission or corrupt JSON): %s", path, error)
        return None


def read_list(path: str) -> list[dict]:
    """Read a file expecting a list (like users or checks).
    Returns the list, or an empty list [] if missing, broken, or not a list."""
    data = read_json(path)
    if isinstance(data, list):
        return data
    if data is not None:                # None was already logged by read_json
        logger.error("%s does not contain a list -- ignoring its contents.", path)
    return []


def write_list(path: str, items: list[dict]) -> bool:
    """Overwrite a file with a full list, creating the data/ folder if needed.
    Returns True if saving succeeded, False if it failed."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(items, file, indent=2)
        return True
    except OSError as error:
        logger.error("Could not write %s: %s", path, error)
        return False


# ======================================================================
# MAINTENANCE RECORDS (data/maintenance_records.json)
# Functions to save, load, query, and clean up completed service checks.
# ======================================================================

def load() -> list[dict]:
    """Return all saved maintenance checks (oldest first)."""
    return read_list(DATA_FILE)


def save(record: dict) -> bool:
    """Append one new finished check to the records file.
    Returns True if successfully saved."""
    records = load()
    records.append(record)
    return write_list(DATA_FILE, records)


def query(filter_fn) -> list[dict]:
    """Return saved checks matching a specific condition.
    Example: query(lambda r: r["part_name"] == "Coolant")"""
    matching = []
    for record in load():
        if filter_fn(record):
            matching.append(record)
    return matching


def same_inputs(record_a: dict, record_b: dict) -> bool:
    """Check if two maintenance checks share identical inputs across REPEAT_FIELDS."""
    for field in REPEAT_FIELDS:
        if record_a.get(field) != record_b.get(field):
            return False
    return True


def find_repeat(record: dict) -> dict | None:
    """Find and return the most recent saved check with identical inputs, or None."""
    matches = query(lambda saved: same_inputs(saved, record))
    if matches:
        return matches[-1]              # Newest match is at the end of the list
    return None


def get_last_odometer(records: list[dict], license_plate: str) -> int | None:
    """Return the highest odometer reading recorded for a car plate, or None if none exist."""
    readings = []
    for record in records:
        odometer = record.get("current_odometer")
        # Ensure license plate matches and mileage is a valid integer number
        if record.get("license_plate") == license_plate and isinstance(odometer, int):
            readings.append(odometer)
    if readings:
        return max(readings)
    return None


def delete_records_for_plate(license_plate: str) -> int:
    """Delete all saved maintenance checks for a specific license plate.
    Called when a user profile is deleted. Returns number of removed checks."""
    records = load()
    remaining = []
    for record in records:
        if record.get("license_plate") != license_plate:
            remaining.append(record)
    removed_count = len(records) - len(remaining)
    if removed_count > 0 and not write_list(DATA_FILE, remaining):
        return 0                        # Write failed, so nothing was deleted
    return removed_count


# ======================================================================
# USER PROFILES (data/users.json)
# Functions to load, find, save, and delete user profiles using license plates.
# ======================================================================

def load_users() -> list[dict]:
    """Return all saved user profiles."""
    return read_list(USERS_FILE)


def find_user_by_plate(license_plate: str) -> dict | None:
    """Find a user profile by license plate, or return None if not found."""
    for user in load_users():
        if user.get("license_plate") == license_plate:
            return user
    return None


def save_user(user: dict) -> bool:
    """Create a new user profile or update an existing one matching the license plate."""
    users_to_keep = []
    for existing in load_users():
        if existing.get("license_plate") != user["license_plate"]:
            users_to_keep.append(existing)
    users_to_keep.append(user)          # Replace old profile with updated details
    return write_list(USERS_FILE, users_to_keep)


def delete_user(license_plate: str) -> bool:
    """Delete a user profile matching the given license plate.
    Returns False if plate wasn't found or saving failed."""
    users = load_users()
    remaining = []
    for user in users:
        if user.get("license_plate") != license_plate:
            remaining.append(user)
    if len(remaining) == len(users):
        return False                    # Plate not found
    return write_list(USERS_FILE, remaining)


# ======================================================================
# CAR SCHEDULES & MANUALS (data/cars.json)
# Functions to read manufacturer schedules and locate PDF manuals.
# ======================================================================

def is_valid_interval(entry) -> bool:
    """Check if a service interval line looks like {"km": 10000, "months": 12} 
    with both values being positive integers."""
    return (isinstance(entry, dict)
            and isinstance(entry.get("km"), int) and entry["km"] > 0
            and isinstance(entry.get("months"), int) and entry["months"] > 0)


def is_valid_car(car) -> bool:
    """Check if a car entry contains a valid model name, condition, manual path, and schedule."""
    return (isinstance(car, dict)
            and isinstance(car.get("model"), str) and car["model"].strip() != ""
            and car.get("condition") in VALID_CONDITIONS
            and isinstance(car.get("manual"), str)
            and isinstance(car.get("schedule"), dict))


def load_cars() -> list[dict]:
    """Load valid cars from cars.json. Invalid entries or schedule lines are safely skipped."""
    data = read_json(CARS_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("cars"), list):
        if data is not None:
            logger.error("%s has no 'cars' list.", CARS_FILE)
        return []

    cars = []
    for car in data["cars"]:
        # Skip invalid car entries while keeping the valid ones
        if not is_valid_car(car):
            logger.error("Skipping invalid car entry in %s: %r", CARS_FILE, car)
            continue

        # Keep only valid schedule lines for this car
        good_schedule = {}
        for part, interval in car["schedule"].items():
            if is_valid_interval(interval):
                good_schedule[part] = interval

        clean_car = dict(car)           # Copy dict to avoid mutating original data
        clean_car["schedule"] = good_schedule
        cars.append(clean_car)
    return cars


def list_car_models() -> list[str]:
    """Return a list of available car model names."""
    names = []
    for car in load_cars():
        names.append(car["model"])
    return names


def find_car(car_model: str) -> dict | None:
    """Find and return a car entry by its model name, or None if not found."""
    for car in load_cars():
        if car["model"] == car_model:
            return car
    return None


def list_parts(car_model: str) -> list[str]:
    """Return an alphabetically sorted list of tracked parts for a car model."""
    car = find_car(car_model)
    if car is None:
        return []
    return sorted(car["schedule"])


def get_service_interval(car_model: str, part_name: str) -> dict | None:
    """Return the service interval dict (e.g. {"interval_km": 10000, "interval_months": 12})
    for a given car model and part."""
    car = find_car(car_model)
    if car is None or part_name not in car["schedule"]:
        return None
    interval = car["schedule"][part_name]
    return {"interval_km": interval["km"], "interval_months": interval["months"]}


def get_manual_path(car_model: str) -> str | None:
    """Return the absolute path to a car model's PDF manual, or None if missing."""
    car = find_car(car_model)
    if car is None:
        return None
    path = os.path.join(PROJECT_DIR, car["manual"])
    if not os.path.isfile(path):
        logger.error("Manual for %s not found at %s", car_model, path)
        return None
    return path