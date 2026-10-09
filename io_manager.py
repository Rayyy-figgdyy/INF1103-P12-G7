"""
io_manager.py -- THE SCREEN AND KEYBOARD (the "Input layer").

(New to Python? See the "How to read Python" guide at the top of main.py.)

WHAT THIS FILE DOES
    Everything the user sees or types goes through this file:
      - it shows the menus and results on screen   (print)
      - it reads what the user types               (input)
      - it CHECKS what the user typed. If something is wrong (e.g. a date
        in the future), it explains the problem and asks again. This is
        called "reject and re-prompt" in our project brief. The app
        never crashes because of a typing mistake.

RULES FROM OUR PROJECT BRIEF (section 2.1)
    - EVERY print() and input() in the whole app lives in this file.
    - It hands clean, checked values back to main.py.
    - It never saves files, never talks to the AI and never makes
      business decisions. If it needs saved information (e.g. which
      license plates are already taken), main.py looks it up and passes
      it in.

Phase 2 of the project (object-oriented version): this file becomes an
IOManager class with InputHandler and OutputHandler sub-classes.
"""
import datetime   # Python's built-in tools for dates
import re         # "regular expressions": a way to check text follows a pattern

# ======================================================================
# FIXED SETTINGS ("constants": written in CAPITALS, never changed by the app)
# ======================================================================

# The menu shown when nobody is logged in. Text between three quote marks
# can span several lines.
MENU_LOGGED_OUT = """
==== Car Maintenance & Service Risk Tracker ====
Not logged in
1) Login
2) Register new profile
3) Exit
"""

# The menu shown when someone is logged in. The {name}, {plate} and
# {car_model} gaps are filled in later with the user's details.
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

# The pattern a Singapore license plate must follow, e.g. SBA1234A:
#   [A-Z]{1,3}  1 to 3 capital letters
#   \d{1,4}     then 1 to 4 digits
#   [A-Z]       then 1 capital letter
#   ^ and $     the WHOLE plate must match, with nothing extra
LICENSE_PLATE_PATTERN = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]$")

# Any odometer reading above this is surely a typing mistake.
MAX_ODOMETER_KM = 2_000_000      # the _ is just to make big numbers readable

# Users type and see dates as DD/MM/YYYY (e.g. 25/12/2026).
# Behind the scenes, dates are SAVED as YYYY-MM-DD (e.g. 2026-12-25),
# because that format sorts correctly and is easy to calculate with.
DATE_FORMAT = "%d/%m/%Y"         # %d = day, %m = month, %Y = 4-digit year
DATE_HINT = "DD/MM/YYYY"         # what the user is shown


# ======================================================================
# SMALL HELPERS for converting and formatting values
# (A name starting with _ means "only used inside this file".)
# ======================================================================

def _parse_date(typed: str) -> datetime.date | None:
    """Turn what the user typed, like '03/09/2025', into a real date.
    Dashes work too ('03-09-2025'). Gives back None if it isn't a real
    date (e.g. '31/02/2025' -- February has no 31st)."""
    try:
        cleaned = typed.replace("-", "/")                 # allow dashes
        return datetime.datetime.strptime(cleaned, DATE_FORMAT).date()
    except ValueError:                                    # not a valid date
        return None


def _show_date(saved_date) -> str:
    """Turn a saved date like '2025-09-03' into '03/09/2025' for the screen.
    If there's no date, show '-'. If the saved value is damaged, show it
    as it is (better than crashing)."""
    if not saved_date:
        return "-"
    try:
        date = datetime.date.fromisoformat(str(saved_date))   # read "YYYY-MM-DD"
        return date.strftime(DATE_FORMAT)                      # write "DD/MM/YYYY"
    except ValueError:
        return str(saved_date)


def _km(value) -> str:
    """Turn a number like 49000 into '49,000' for the screen.
    If the value is missing, show '-'. If it's damaged, show it as it is.
    (This stops the history screen crashing on one bad saved record.)"""
    if value is None or value == "":
        return "-"
    # isinstance(value, int) asks "is this value a whole number?".
    # True/False count as numbers in Python, so they are excluded.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.0f}"            # :,.0f = add commas, no decimals
    if isinstance(value, str) and value.strip().isdigit():
        return f"{int(value):,}"          # a number saved as text, e.g. "52000"
    return str(value)


def _normalise_plate(typed: str) -> str:
    """Tidy up a typed license plate so small differences don't matter:
    'sba 1234-a' becomes 'SBA1234A' (spaces and dashes removed, all capitals)."""
    return typed.replace(" ", "").replace("-", "").upper()


# ======================================================================
# QUESTIONS THAT KEEP ASKING UNTIL THE ANSWER IS VALID
# Each one uses a `while True` loop: it repeats until a `return` hands
# back a valid answer. Wrong answers get a "->" message and are asked again.
# ======================================================================

def _prompt_text(label: str) -> str:
    """Ask for any text that isn't empty (e.g. a name)."""
    while True:
        answer = input(f"{label}: ").strip()     # .strip() removes spaces at the ends
        if answer:                               # true if not empty
            return answer
        print(f"  -> {label} cannot be empty. Please try again.")


def _prompt_km(label: str) -> int:
    """Ask for a whole number of kilometres, e.g. 45200 (45,200 also works)."""
    while True:
        typed = input(f"{label}: ").strip().replace(",", "")   # ignore commas
        # .isdigit() is True only if every character is 0-9, so it also
        # rejects negative numbers, decimals and letters.
        if not typed.isdigit():
            print(f"  -> {label} must be a whole number of km, e.g. 45200. Please try again.")
        elif int(typed) > MAX_ODOMETER_KM:
            print(f"  -> {label} looks too large (max {MAX_ODOMETER_KM:,} km). Please try again.")
        else:
            return int(typed)          # int(...) turns the text "45200" into the number 45200


def _prompt_past_date(label: str) -> str:
    """Ask for a real date that isn't in the future (e.g. a past service date).
    Gives it back in the saved format, YYYY-MM-DD."""
    while True:
        date = _parse_date(input(f"{label} ({DATE_HINT}): ").strip())
        if date is None:
            print(f"  -> {label} must be a real date in {DATE_HINT} format. Please try again.")
        elif date > datetime.date.today():
            print(f"  -> {label} cannot be in the future. Please try again.")
        else:
            return date.isoformat()    # isoformat() = "YYYY-MM-DD"


def _prompt_number_choice(label: str, options: list[str]) -> str:
    """Show a numbered list (1, 2, 3...) and give back the option picked."""
    # enumerate(..., start=1) gives each option a number, starting from 1.
    for number, option in enumerate(options, start=1):
        print(f"  {number:>2}) {option}")          # :>2 lines the numbers up
    while True:
        typed = input(f"{label}: ").strip()
        if typed.isdigit() and 1 <= int(typed) <= len(options):
            # Python counts list positions from 0, but our menu starts at 1,
            # so choice 1 is position 0.
            return options[int(typed) - 1]
        print(f"  -> Please enter a number from 1 to {len(options)}.")


def confirm_action(question: str) -> bool:
    """Ask a yes/no question. Gives back True for yes, False for no."""
    while True:
        answer = input(f"{question} (y/n): ").strip().lower()   # .lower() so 'Y' works too
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  -> Please answer y or n.")


# ======================================================================
# THE MAIN MENU
# ======================================================================

def prompt_main_menu(current_user: dict | None) -> str:
    """Show the right menu (logged in or not) and give back what was chosen."""
    if current_user is None:
        print(MENU_LOGGED_OUT)
    else:
        # .format(...) fills the {name}, {plate} and {car_model} gaps.
        print(MENU_LOGGED_IN.format(name=current_user["name"],
                                    plate=current_user["license_plate"],
                                    car_model=current_user["car_model"]))
    return input("Choose an option: ").strip()


# ======================================================================
# ACCOUNT QUESTIONS: login, register, update
# ======================================================================

def prompt_login() -> str:
    """Ask for the license plate to log in with."""
    print("\n--- Login ---")           # \n = start a new line (adds a blank line)
    while True:
        plate = _normalise_plate(input("License plate: "))
        if plate:
            return plate
        print("  -> License plate cannot be empty. Please try again.")


def prompt_identity(taken_plates: set[str]) -> dict:
    """Ask for a name and a NEW license plate when registering.
    taken_plates = plates already registered (looked up by main.py),
    so a duplicate can be refused here."""
    print("\n--- Register new profile ---")
    name = _prompt_text("Name")
    while True:
        plate = _normalise_plate(input("License plate (e.g. SBA1234A): "))
        if not LICENSE_PLATE_PATTERN.match(plate):       # doesn't fit the pattern
            print("  -> License plate must look like SBA1234A (letters, digits, then a letter). Please try again.")
        elif plate in taken_plates:                      # someone already has it
            print(f"  -> {plate} is already registered. Please try again.")
        else:
            return {"name": name, "license_plate": plate}


def prompt_car_choice(car_models: list[str]) -> str:
    """Ask which of the supported cars the user drives (the list comes from cars.json)."""
    print("\nWhich car do you drive?")
    return _prompt_number_choice("Car number", car_models)


def prompt_coe_expiry() -> str:
    """Ask for the COE expiry date. A date in the past IS allowed: an
    expired COE is real information, and our rules act on it."""
    while True:
        coe = _parse_date(input(f"COE expiry date ({DATE_HINT}): ").strip())
        if coe:
            return coe.isoformat()
        print(f"  -> COE expiry date must be a real date in {DATE_HINT} format. Please try again.")


def prompt_updated_profile(current: dict) -> dict:
    """Let the user change their name and COE date.
    Pressing Enter without typing keeps the old value.
    The license plate and car can't be changed: they identify the account."""
    print("\n--- Update profile (press Enter to keep the current value) ---")
    print(f"License plate: {current['license_plate']} (cannot be changed)")
    print(f"Car:           {current['car_model']} ({current['car_condition']}) (cannot be changed)")

    updated = dict(current)   # a copy, so the original stays unchanged until saved

    new_name = input(f"Name [{current['name']}]: ").strip()
    if new_name:                       # only change it if something was typed
        updated["name"] = new_name

    while True:
        typed = input(f"COE expiry date ({DATE_HINT}) [{_show_date(current['coe_expiry_date'])}]: ").strip()
        if not typed:
            break                      # Enter on its own = keep the old date
        coe = _parse_date(typed)
        if coe:
            updated["coe_expiry_date"] = coe.isoformat()
            break                      # leave the loop: we have a valid date
        print(f"  -> Use {DATE_HINT}, or press Enter to keep.")
    return updated


def display_user(user: dict) -> None:
    """Show one user's profile."""
    print("\n--- Profile ---")
    print(f"Name:            {user.get('name')}")
    print(f"License plate:   {user.get('license_plate')}")
    print(f"Car model:       {user.get('car_model')}")
    print(f"Car condition:   {user.get('car_condition')}")
    print(f"COE expiry date: {_show_date(user.get('coe_expiry_date'))}")


# ======================================================================
# MAINTENANCE CHECK QUESTIONS
# ======================================================================

def prompt_part_choice(parts: list[str]) -> str:
    """Ask which part to check (only parts with a stored interval are listed)."""
    print("\nWhich part are you checking?")
    return _prompt_number_choice("Part number", parts)


def prompt_service_details(last_known_odometer: int | None) -> dict:
    """Ask for the details from the last workshop invoice, plus today's
    odometer reading.

    This is where our BUSINESS RULE 1 (input validation) is enforced:
      - today's odometer can't be lower than the odometer at the last service
      - today's odometer can't be lower than the last reading we have on file
    last_known_odometer is that last reading (looked up by main.py),
    or None if this car has no saved checks yet."""
    print("\nFrom your last workshop invoice for this part:")
    last_service_date = _prompt_past_date("Date of last service")
    last_service_mileage = _prompt_km("Odometer at last service (km)")

    if last_known_odometer is not None:
        print(f"(Last odometer reading on file for this car: {last_known_odometer:,} km)")

    while True:
        current_odometer = _prompt_km("Current odometer reading (km)")
        if current_odometer < last_service_mileage:
            print(f"  -> Can't be lower than the odometer at last service ({last_service_mileage:,} km). Please try again.")
        elif last_known_odometer is not None and current_odometer < last_known_odometer:
            print(f"  -> Can't be lower than the last reading on file ({last_known_odometer:,} km). Please try again.")
        else:
            break                      # the reading is valid

    # Hand back the three answers as one dictionary.
    return {
        "last_service_date": last_service_date,
        "last_service_mileage": last_service_mileage,
        "current_odometer": current_odometer,
    }


# ======================================================================
# SHOWING RESULTS (our brief asks for display_record, display_list and
# display_result). They use .get(), _km() and _show_date(), so a record
# with missing or damaged information is still shown instead of crashing.
# ======================================================================

def display_message(message: str) -> None:
    """Show any one-line message. (main.py isn't allowed to print, so it
    sends its messages here.)"""
    print(message)


def display_record(record: dict) -> None:
    """Show one maintenance check in full."""
    urgency = record.get("urgency_score")
    if urgency is None:
        urgency_text = "n/a"            # the AI couldn't answer
    else:
        urgency_text = f"{urgency}/10"

    print("\n--- Maintenance check ---")
    print(f"Part:               {record.get('part_name')}  ({record.get('car_model')}, {record.get('license_plate')})")
    print(f"Checked on:         {_show_date(record.get('check_date'))} at {_km(record.get('current_odometer'))} km")
    print(f"Last serviced:      {_show_date(record.get('last_service_date'))} at {_km(record.get('last_service_mileage'))} km")
    print(f"Schedule:           every {_km(record.get('interval_km'))} km or {record.get('interval_months')} months")
    print(f"Since last service: {_km(record.get('km_since_service'))} km / {record.get('days_since_service')} days")
    if record.get("is_overdue"):        # this line is only shown for overdue parts
        print(f"Overdue by:         {_km(record.get('km_overdue'))} km / {record.get('days_overdue')} days")
    print(f"AI urgency:         {urgency_text} (confidence: {record.get('confidence')})")
    print(f"Answered by:        {record.get('ai_provider', 'n/a')}")
    print(f"AI summary:         {record.get('overdue_summary')}")
    print(f"Manual reference:   {record.get('manual_reference')}")
    print(f"OUTCOME:            {record.get('outcome')}")
    print(f"Service by:         {_show_date(record.get('recommended_service_date'))}")
    print(f"Risk score:         {record.get('risk_score')}/100")
    print(f"Advice:             {record.get('recommended_action')}")


def display_list(records: list[dict]) -> None:
    """Show many checks as a table, one line each (history and urgent views)."""
    if not records:
        print("\nNo records to show.")
        return

    # Column widths: :<12 = "left-align in a 12-character column",
    # :>10 = "right-align in a 10-character column". This lines up the table.
    print(f"\n{'Checked':<12}{'Part':<20}{'Odometer':>10}  {'Urgency':<8}{'Service by':<12}Outcome")
    for r in records:                   # one table row per saved check
        urgency = r.get("urgency_score")
        urgency_text = "-" if urgency is None else str(urgency)
        part = str(r.get("part_name"))[:19]          # cut long names to 19 characters
        print(f"{_show_date(r.get('check_date')):<12}"
              f"{part:<20}"
              f"{_km(r.get('current_odometer')):>10}  "
              f"{urgency_text:<8}"
              f"{_show_date(r.get('recommended_service_date')):<12}"
              f"{r.get('outcome')}")


def display_result(record: dict) -> None:
    """Show a check that has just been saved."""
    print("\n>>> Assessment saved.")
    display_record(record)


def display_repeated(previous: dict) -> None:
    """Shown instead of asking the AI again, when the user types exactly
    the same details as an earlier check."""
    print("\nThis is repeated information.")
    print(f"You checked this part with the same details on {_show_date(previous.get('check_date'))}, "
          "so it was not sent to the AI again. Your saved assessment:")
    display_record(previous)