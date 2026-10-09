"""
data_manager.py -- SAVING AND LOADING DATA (the "Data layer", the app's memory).

(New to Python? See the "How to read Python" guide at the top of main.py.)

WHAT THIS FILE DOES
    Everything the app needs to remember is kept in files in the data/
    folder, so nothing is lost when the app is closed:
      maintenance_records.json   every maintenance check that was done
      users.json                 every user profile (one per license plate)
      cars.json                  our 4 supported cars: Brand New/Used, where
                                 the manual is, and how often each part
                                 needs servicing (business rule 2)

    The files use JSON, a simple text format for storing lists and
    dictionaries. You can open them in any text editor to look inside.

RULES FROM OUR PROJECT BRIEF (section 2.4)
    - save(record): add a finished check to the file
    - load():       read all saved checks (an empty list if there are none)
    - query(filter_fn): find the saved checks that pass a test
    - If a file is missing or damaged, DON'T crash: record the problem,
      carry on with an empty list. (Hard constraint C4: data is kept in
      flat files, so the app gives the same results each time it runs.)
    - Saving and loading only: no business rules, no print().

Phase 2 of the project (object-oriented version): this file becomes a
DataManager class with save(), load() and query() methods.
"""
import json        # reads and writes the JSON file format
import logging     # records errors
import os          # works with folders and file paths

logger = logging.getLogger("data_manager")

# ======================================================================
# WHERE THE FILES ARE
# The paths are worked out from where THIS file is, so the app finds its
# data no matter which folder it's started from (including in Docker).
# ======================================================================

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))   # the project folder
DATA_DIR = os.path.join(PROJECT_DIR, "data")               # .../data
DATA_FILE = os.path.join(DATA_DIR, "maintenance_records.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
CARS_FILE = os.path.join(DATA_DIR, "cars.json")

VALID_CONDITIONS = ("Brand New", "Used")

# A check counts as "repeated" when ALL five of these match a saved check.
REPEAT_FIELDS = ("license_plate", "part_name", "last_service_date",
                 "last_service_mileage", "current_odometer")


# ======================================================================
# READING AND WRITING FILES SAFELY
# ======================================================================

def _read_json(path: str):
    """Read a JSON file and give back what's in it.
    Gives back None if the file doesn't exist, is empty, or is damaged.
    It never crashes: problems are recorded in the log instead."""
    if not os.path.exists(path):
        return None                     # no file yet: normal the first time
    try:
        with open(path, "r", encoding="utf-8") as file:   # "r" = open for reading
            content = file.read().strip()
        if not content:
            return None                 # the file is empty
        return json.loads(content)      # turn the JSON text into Python lists/dictionaries
    except (OSError, json.JSONDecodeError) as error:
        # OSError = the file couldn't be opened (e.g. no permission).
        # JSONDecodeError = the file's contents aren't valid JSON (damaged).
        logger.error("Could not read %s (missing permission or corrupt JSON): %s", path, error)
        return None


def _read_list(path: str) -> list[dict]:
    """Read a file that should contain a list (e.g. all saved checks).
    If the file is missing, damaged or holds something else, gives back
    an empty list [] so the app can carry on."""
    data = _read_json(path)
    if isinstance(data, list):          # is it really a list?
        return data
    if data is not None:
        logger.error("%s does not contain a list -- ignoring its contents.", path)
    return []


def _write_list(path: str, items: list[dict]) -> bool:
    """Save a list to a file, replacing what was there before.
    Gives back True if it worked, False if it didn't."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)     # create the data/ folder if needed
        with open(path, "w", encoding="utf-8") as file:       # "w" = open for writing
            json.dump(items, file, indent=2)                  # indent=2 keeps the file readable
        return True
    except OSError as error:
        logger.error("Could not write %s: %s", path, error)
        return False


# ======================================================================
# MAINTENANCE CHECKS: save, load and query (from our project brief)
# ======================================================================

def load() -> list[dict]:
    """Give back every saved maintenance check (an empty list if none yet)."""
    return _read_list(DATA_FILE)


def save(record: dict) -> bool:
    """Add one finished check to the file.
    How: read all saved checks, add the new one at the end, write them all back.
    Gives back True if it was saved."""
    records = load()
    records.append(record)              # .append adds an item to the end of a list
    return _write_list(DATA_FILE, records)


def query(filter_fn) -> list[dict]:
    """Find saved checks that pass a test.
    filter_fn is a small function that looks at one check and answers
    True (keep it) or False (skip it). For example, to find battery checks:
        query(lambda r: r["part_name"] == "Battery")"""
    matching = []
    for record in load():
        if filter_fn(record):
            matching.append(record)
    return matching


def _same_inputs(record_a: dict, record_b: dict) -> bool:
    """True if two checks have exactly the same inputs (all of REPEAT_FIELDS)."""
    for field in REPEAT_FIELDS:
        if record_a.get(field) != record_b.get(field):    # != means "is not equal to"
            return False
    return True


def find_repeat(record: dict) -> dict | None:
    """Has the user typed in exactly these details before?
    Gives back the most recent saved check with the same inputs, or None."""
    matches = query(lambda saved: _same_inputs(saved, record))
    if matches:
        return matches[-1]          # [-1] = the last item in a list (the most recent)
    return None


def get_last_odometer(records: list[dict], license_plate: str) -> int | None:
    """The highest odometer reading we have for this car, or None if there
    are no readings yet. (The odometer belongs to the whole car, not one
    part, so readings for every part count.)
    A damaged reading (e.g. a number saved as text) is skipped."""
    readings = []
    for record in records:
        odometer = record.get("current_odometer")
        if record.get("license_plate") == license_plate and isinstance(odometer, int):
            readings.append(odometer)
    if readings:
        return max(readings)        # max = the biggest number
    return None


def delete_records_for_plate(license_plate: str) -> int:
    """Delete every saved check for one car. Gives back how many were deleted."""
    records = load()
    remaining = []
    for record in records:
        if record.get("license_plate") != license_plate:   # keep other cars' checks
            remaining.append(record)
    removed_count = len(records) - len(remaining)
    if removed_count > 0 and not _write_list(DATA_FILE, remaining):
        return 0                    # saving failed, so nothing was really deleted
    return removed_count


# ======================================================================
# USER PROFILES: Create, Read, Update, Delete ("CRUD")
# Each profile is identified by its license plate (no two profiles share one).
# ======================================================================

def load_users() -> list[dict]:
    """READ: give back every user profile."""
    return _read_list(USERS_FILE)


def find_user_by_plate(license_plate: str) -> dict | None:
    """READ: give back the profile with this license plate, or None."""
    for user in load_users():
        if user.get("license_plate") == license_plate:
            return user
    return None


def save_user(user: dict) -> bool:
    """CREATE a new profile, or UPDATE an existing one.
    How: keep every OTHER user, add this one, and save. (If this user was
    already in the file, their old version is left out, so it's replaced.)
    Gives back True if it was saved."""
    users_to_keep = []
    for existing in load_users():
        if existing.get("license_plate") != user["license_plate"]:
            users_to_keep.append(existing)
    users_to_keep.append(user)
    return _write_list(USERS_FILE, users_to_keep)


def delete_user(license_plate: str) -> bool:
    """DELETE the profile with this license plate.
    Gives back False if there was no such profile, or saving failed."""
    users = load_users()
    remaining = []
    for user in users:
        if user.get("license_plate") != license_plate:
            remaining.append(user)
    if len(remaining) == len(users):
        return False                # nothing was removed: the plate wasn't found
    return _write_list(USERS_FILE, remaining)


# ======================================================================
# THE CAR LIST (data/cars.json): our 4 supported cars
# BUSINESS RULE 2: how often each part needs servicing comes from here.
# ======================================================================

def _is_valid_interval(entry) -> bool:
    """One line of a car's schedule must look like {"km": 10000, "months": 12},
    with both numbers above zero."""
    return (isinstance(entry, dict)
            and isinstance(entry.get("km"), int) and entry["km"] > 0
            and isinstance(entry.get("months"), int) and entry["months"] > 0)


def _is_valid_car(car) -> bool:
    """A car entry must have a model name, Brand New/Used, a manual file
    name and a schedule."""
    return (isinstance(car, dict)
            and isinstance(car.get("model"), str) and car["model"].strip() != ""
            and car.get("condition") in VALID_CONDITIONS
            and isinstance(car.get("manual"), str)
            and isinstance(car.get("schedule"), dict))


def load_cars() -> list[dict]:
    """Give back every valid car from cars.json, in the file's order.
    A car with a mistake in it is skipped, and a schedule line with a
    mistake is left out (both are logged), so one typo doesn't stop the
    whole app. Gives back [] if the file is missing or damaged."""
    data = _read_json(CARS_FILE)
    if not isinstance(data, dict) or not isinstance(data.get("cars"), list):
        if data is not None:
            logger.error("%s has no 'cars' list.", CARS_FILE)
        return []

    cars = []
    for car in data["cars"]:
        if not _is_valid_car(car):
            logger.error("Skipping invalid car entry in %s: %r", CARS_FILE, car)
            continue                    # skip this car, go on to the next one

        good_schedule = {}
        for part, interval in car["schedule"].items():   # .items() = each name and its value
            if _is_valid_interval(interval):
                good_schedule[part] = interval

        clean_car = dict(car)                  # a copy of the car...
        clean_car["schedule"] = good_schedule  # ...with only the valid schedule lines
        cars.append(clean_car)
    return cars


def list_car_models() -> list[str]:
    """The car names to choose from when registering."""
    names = []
    for car in load_cars():
        names.append(car["model"])
    return names


def find_car(car_model: str) -> dict | None:
    """Give back the cars.json entry for this car name, or None."""
    for car in load_cars():
        if car["model"] == car_model:
            return car
    return None


def list_parts(car_model: str) -> list[str]:
    """The parts we have a service interval for on this car, in A-Z order."""
    car = find_car(car_model)
    if car is None:
        return []
    return sorted(car["schedule"])      # sorted() puts the part names in A-Z order


def get_service_interval(car_model: str, part_name: str) -> dict | None:
    """How often this part needs servicing on this car, e.g.
    {"interval_km": 10000, "interval_months": 12}. None if we don't have it."""
    car = find_car(car_model)
    if car is None or part_name not in car["schedule"]:
        return None
    interval = car["schedule"][part_name]
    return {"interval_km": interval["km"], "interval_months": interval["months"]}


def get_manual_path(car_model: str) -> str | None:
    """Where this car's manual (PDF file) is on the computer.
    Gives back None if the car isn't in our list, or the PDF isn't in the
    manuals/ folder (that problem is also logged)."""
    car = find_car(car_model)
    if car is None:
        return None
    path = os.path.join(PROJECT_DIR, car["manual"])
    if not os.path.isfile(path):        # is there really a file there?
        logger.error("Manual for %s not found at %s", car_model, path)
        return None
    return path