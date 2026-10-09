"""
main.py -- THE STARTING POINT OF THE APP.  Run it with:   python main.py

WHAT THIS APP DOES
    A car owner logs in, picks a car part (e.g. Engine Oil) and types in
    details from their last workshop invoice. An AI reads the car's
    owner's manual and says how urgently that part needs servicing.
    Our own rules then turn that into an outcome like "URGENT" or "OK".

HOW THE APP IS SPLIT UP (the "four managers" from our project brief)
    Each file has ONE job, like stations on a factory line:

    User -> io_manager -> ai_manager -> logic_manager -> data_manager
            (screen &      (asks the     (applies our     (saves and
             keyboard)      AI)           business rules)  loads files)

    main.py is the "supervisor": it is the only file that talks to all
    four managers and passes information between them.

----------------------------------------------------------------------
HOW TO READ PYTHON (a quick guide for non-programmers)
----------------------------------------------------------------------
  # text            A COMMENT: a note for humans. Python ignores it.
  (3 quote marks)   A DOCSTRING: text between two sets of three quote marks,
                    like this whole block. A longer note describing a file
                    or a function. Python ignores it too.
  name = value      A VARIABLE: a labelled box that stores a value.
  def name(...):    A FUNCTION: a named set of steps. The indented lines
                    underneath are the steps. Things in the brackets are
                    the inputs it needs ("parameters").
  return value      Ends a function and hands a result back to whoever
                    called it.
  name(...)         CALLING a function: "do those steps now".
  io_manager.x(...) Call the function x that lives in the file io_manager.py.
  if / elif / else  A DECISION: do something only if a condition is true.
                    elif = "else if", checked only when the ones above failed.
  while True:       A LOOP that repeats forever, until a `break` or `return`.
  for x in items:   A LOOP that repeats once for each item in a list.
  [a, b, c]         A LIST: items in order.
  {"key": value}    A DICTIONARY: values looked up by name, like a form
                    with labelled boxes. record["part_name"] reads one box.
  record.get("x")   Same as record["x"], but gives None instead of
                    crashing if the box doesn't exist.
  None              Python's word for "nothing / no value".
  f"Hi {name}"      An F-STRING: text with a value slotted into the { }.
  try / except      "Try this; if an error happens, do this instead of
                    crashing."
  str | None        A TYPE HINT: says what kind of value is expected
                    (here: text, or nothing). Python doesn't enforce it;
                    it's documentation for humans.
----------------------------------------------------------------------

PROJECT RULES THIS FILE FOLLOWS
    - Hard constraint C1, "100% procedural": only functions, no classes.
    - The managers never talk to each other directly; only main.py
      connects them.
    - The logged-in user is kept in one variable, `current_user`, inside
      main() and handed to each function that needs it (no global variables).
"""

# "import" loads code from other files so we can use it here.
import datetime          # Python's built-in tools for dates
import logging           # Python's built-in tools for recording errors

import ai_manager        # our file that talks to the AI
import data_manager      # our file that saves and loads data
import io_manager        # our file that shows the screen and reads the keyboard
import logic_manager     # our file with the business rules


# ======================================================================
# THE PIPELINE: one maintenance check, from start to finish
# ======================================================================

def build_record(raw_record: dict, manual_path: str | None = None) -> dict:
    """Send one check through the AI, then through our business rules.

    Inputs:
        raw_record  -- a dictionary of what the user typed, plus car details
        manual_path -- where the car's owner's manual (PDF) is on the computer
    Gives back:
        the finished record, ready to save and show on screen.
    """
    # STEP 1 -- Ask the AI (ai_manager). Every check MUST go through the AI
    # (hard constraint C2). The AI adds: urgency score, confidence,
    # a short summary, advice, and where in the manual it looked.
    enriched = ai_manager.process(raw_record, manual_path=manual_path)

    # STEP 2 -- Work out the facts (logic_manager.evaluate): km and days
    # since the last service, whether the part is overdue, and the date
    # it should be serviced by.
    final_record = dict(enriched)                          # make a copy to add to
    final_record.update(logic_manager.evaluate(enriched))  # add the new facts

    # STEP 3 -- A number for sorting (score) and the final decision (route).
    final_record["risk_score"] = logic_manager.score(final_record)
    final_record["outcome"] = logic_manager.route(final_record)
    return final_record


def check_part_flow(user: dict) -> None:
    """Menu option 1: check one part of the logged-in user's car.
    (-> None means this function doesn't hand anything back.)"""
    car_model = user["car_model"]

    # The AI has to read this car's manual. If the PDF file isn't there,
    # explain the problem and stop (return) instead of crashing.
    manual_path = data_manager.get_manual_path(car_model)
    if manual_path is None:
        io_manager.display_message(f"The manual for {car_model} is missing, so parts can't be checked.")
        return

    # Get the list of parts we have a service interval for (business rule 2:
    # intervals must come from our stored manufacturer schedule, cars.json).
    parts = data_manager.list_parts(car_model)
    if not parts:                       # "not parts" means the list is empty
        io_manager.display_message("No service schedule is available, so parts can't be checked.")
        return

    # Let the user pick a part, then look up its interval (e.g. 10,000 km or 12 months).
    part_name = io_manager.prompt_part_choice(parts)
    interval = data_manager.get_service_interval(car_model, part_name)
    if interval is None:
        io_manager.display_message(f"No stored service interval for {part_name}.")
        return

    # Find this car's highest odometer reading so far, so the screen can
    # refuse a new reading that goes backwards. main.py fetches it and hands
    # it over, because io_manager is not allowed to read the data files.
    last_odometer = data_manager.get_last_odometer(data_manager.load(), user["license_plate"])
    details = io_manager.prompt_service_details(last_odometer)

    # Put everything the AI needs into one dictionary.
    raw_record = {
        "license_plate": user["license_plate"],
        "car_model": car_model,
        "car_condition": user["car_condition"],
        "coe_expiry_date": user["coe_expiry_date"],
        "part_name": part_name,
        "check_date": datetime.date.today().isoformat(),   # today, as "2026-10-09"
    }
    raw_record.update(details)    # adds: last service date, last service km, current km
    raw_record.update(interval)   # adds: interval_km, interval_months

    # Has the user typed in exactly the same details before? Then show the
    # answer we already saved instead of asking the AI again (saves time
    # and API usage, and gives the same answer as last time).
    previous = data_manager.find_repeat(raw_record)
    if previous is not None:
        io_manager.display_repeated(previous)
        return

    io_manager.display_message("\nAsking the AI to check this part against your manual...")
    final_record = build_record(raw_record, manual_path)

    # Save the result to the file, then show it.
    saved_ok = data_manager.save(final_record)
    if not saved_ok:
        io_manager.display_message("Warning: the assessment could not be saved to disk.")
    io_manager.display_result(final_record)


def history_flow(user: dict) -> None:
    """Menu option 2: show every saved check for this user's car.

    data_manager.query(...) needs a "filter": a small test that says
    whether to keep each saved record. `lambda r: ...` is a quick way to
    write a tiny one-line function. In plain English this one says:
    "for each record r, keep it if its license plate is this user's"."""
    my_records = data_manager.query(lambda r: r.get("license_plate") == user["license_plate"])
    io_manager.display_list(my_records)


def urgent_flow(user: dict) -> None:
    """Menu option 3: only the checks that need attention, most serious first."""
    # Keep records that are this user's AND need attention.
    urgent_records = data_manager.query(
        lambda r: r.get("license_plate") == user["license_plate"] and logic_manager.is_urgent(r)
    )
    # Sort by risk score, biggest first (reverse=True means "largest first").
    urgent_records.sort(key=lambda r: r.get("risk_score", 0), reverse=True)
    io_manager.display_list(urgent_records)


# ======================================================================
# ACCOUNTS: log in, and Create / Read / Update / Delete a profile ("CRUD")
# ======================================================================

def login_flow() -> dict | None:
    """Log in by license plate.
    Gives back the user's profile, or None if no profile has that plate."""
    plate = io_manager.prompt_login()
    user = data_manager.find_user_by_plate(plate)
    if user is None:
        io_manager.display_message(f"No profile for {plate}. Choose 'Register new profile' first.")
        return None
    io_manager.display_message(f"\nWelcome back, {user['name']}!")
    return user


def register_flow() -> dict | None:
    """CREATE a new profile. Gives back the new profile (the user is then
    logged in), or None if something went wrong."""
    car_models = data_manager.list_car_models()
    if not car_models:
        io_manager.display_message("The car list (data/cars.json) is missing, so you can't register yet.")
        return None

    # Collect all plates already registered, so the screen can refuse a
    # duplicate. { ... for u in ... } builds a "set": a collection with
    # no repeats, which is quick to check "is this plate in it?".
    taken_plates = {u.get("license_plate") for u in data_manager.load_users()}

    identity = io_manager.prompt_identity(taken_plates)     # name + license plate
    car_model = io_manager.prompt_car_choice(car_models)    # one of the 4 cars

    profile = {
        "name": identity["name"],
        "license_plate": identity["license_plate"],
        "car_model": car_model,
        # Brand New / Used comes from our car list, not from the user.
        "car_condition": data_manager.find_car(car_model)["condition"],
        "coe_expiry_date": io_manager.prompt_coe_expiry(),
    }

    if not data_manager.save_user(profile):
        io_manager.display_message("Could not save your profile. Please try again.")
        return None
    io_manager.display_message("\nProfile created -- you are now logged in.")
    io_manager.display_user(profile)
    return profile


def update_profile_flow(user: dict) -> dict:
    """UPDATE the user's name or COE date.
    Gives back the updated profile (or the old one if saving failed)."""
    updated = io_manager.prompt_updated_profile(user)
    if not data_manager.save_user(updated):
        io_manager.display_message("Could not save your changes. Please try again.")
        return user
    io_manager.display_message("\nProfile updated.")
    io_manager.display_user(updated)
    return updated


def delete_profile_flow(user: dict) -> dict | None:
    """DELETE the profile and all its saved checks, after asking y/n.
    Gives back None if deleted (so the user is logged out),
    or the same user if they changed their mind."""
    plate = user["license_plate"]
    # len(...) counts the items in a list.
    record_count = len(data_manager.query(lambda r: r.get("license_plate") == plate))

    question = f"Permanently delete {plate} and its {record_count} maintenance record(s)?"
    if not io_manager.confirm_action(question):
        io_manager.display_message("Delete cancelled.")
        return user

    if not data_manager.delete_user(plate):
        io_manager.display_message("Could not delete the profile. Please try again.")
        return user
    data_manager.delete_records_for_plate(plate)
    io_manager.display_message("Profile and records deleted. You have been logged out.")
    return None


# ======================================================================
# THE MAIN LOOP: show the menu, do what the user picks, repeat
# ======================================================================

def _hide_google_notices(record: logging.LogRecord) -> bool:
    """Google's AI library prints its own notices (warnings) that would
    clutter the screen. This "filter" hides them, but still shows real
    errors and all of our own app's messages.
    Gives back True = show this message, False = hide it."""
    from_google = record.name.startswith("google_genai")
    is_minor = record.levelno < logging.ERROR      # a warning, not an error
    return not (from_google and is_minor)


def main() -> None:
    # Set up "logging": how errors are reported. Only io_manager may use
    # print(), so the other files report problems through logging instead.
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_hide_google_notices)

    current_user = None   # None = nobody is logged in yet

    try:
        while True:   # keep showing the menu until the user chooses Exit
            choice = io_manager.prompt_main_menu(current_user)

            if current_user is None:
                # ---------- Menu when NOT logged in ----------
                if choice == "1":
                    current_user = login_flow()
                elif choice == "2":
                    current_user = register_flow()
                elif choice == "3":
                    io_manager.display_message("Goodbye!")
                    break    # leave the loop, which ends the app
                else:
                    io_manager.display_message("Invalid choice. Please enter a number from the menu.")
            else:
                # ---------- Menu when logged in ----------
                if choice == "1":
                    check_part_flow(current_user)
                elif choice == "2":
                    history_flow(current_user)
                elif choice == "3":
                    urgent_flow(current_user)
                elif choice == "4":
                    io_manager.display_user(current_user)
                elif choice == "5":
                    current_user = update_profile_flow(current_user)
                elif choice == "6":
                    current_user = delete_profile_flow(current_user)
                elif choice == "7":
                    io_manager.display_message(f"Logged out {current_user['name']}.")
                    current_user = None
                elif choice == "8":
                    io_manager.display_message("Goodbye!")
                    break
                else:
                    io_manager.display_message("Invalid choice. Please enter a number from the menu.")

    except (KeyboardInterrupt, EOFError):
        # KeyboardInterrupt = the user pressed Ctrl+C.
        # EOFError = there's no keyboard to read from (e.g. Docker run
        # without -it). Either way: exit politely instead of crashing.
        io_manager.display_message("\nExiting. (If you're using Docker, run with: docker run -it ...)")


# This line means: "only start the app if this file was run directly"
# (python main.py) -- not when the test files load it to test it.
if __name__ == "__main__":
    main()