"""
main.py -- entry point of the Car Maintenance & Service Risk Tracker.
Run it with: python main.py

Shows the menu, handles login and profile CRUD, and runs each part check through
the pipeline:  io_manager -> ai_manager -> logic_manager -> data_manager.
main.py is the only file that talks to all four managers; they never call each other.
Procedural only (C1: no classes); the logged-in user lives in main() as current_user.

See explanations/main_explained.txt for a full walkthrough.
"""

import datetime
import logging

import ai_manager
import data_manager
import io_manager
import logic_manager


# ======================================================================
# THE PIPELINE: one maintenance check, from start to finish
# ======================================================================

def build_record(raw_record: dict, manual_path: str | None = None) -> dict:
    """Run one check through the AI, then our business rules.
    Returns the finished record with facts, risk_score and outcome added."""
    # C2: every check goes through the AI. If no AI answers, ai_manager fills in
    # "AI unavailable" values, so route() later gives NEEDS MANUAL REVIEW.
    enriched = ai_manager.process(raw_record, manual_path=manual_path)

    # Add our own facts: km/days since service, overdue flags, service-by date.
    final_record = dict(enriched)
    final_record.update(logic_manager.evaluate(enriched))

    # score() is only used to sort the urgent list; route() makes the decision.
    final_record["risk_score"] = logic_manager.score(final_record)
    final_record["outcome"] = logic_manager.route(final_record)
    return final_record


def check_part_flow(user: dict) -> None:
    """Menu option 1: check one part of the logged-in user's car.
    Saves and shows the result (or the earlier answer for repeated inputs)."""
    car_model = user["car_model"]

    # The AI needs this car's manual PDF; stop early rather than crash without it.
    manual_path = data_manager.get_manual_path(car_model)
    if manual_path is None:
        io_manager.display_message(f"The manual for {car_model} is missing, so parts can't be checked.")
        return

    # Business rule 2: intervals must come from the stored schedule (cars.json).
    parts = data_manager.list_parts(car_model)
    if not parts:
        io_manager.display_message("No service schedule is available, so parts can't be checked.")
        return

    part_name = io_manager.prompt_part_choice(parts)
    interval = data_manager.get_service_interval(car_model, part_name)
    if interval is None:
        io_manager.display_message(f"No stored service interval for {part_name}.")
        return

    # Business rule 1: the new odometer reading can't go backwards. main.py looks up
    # the last reading because io_manager is not allowed to read the data files.
    last_odometer = data_manager.get_last_odometer(data_manager.load(), user["license_plate"])
    details = io_manager.prompt_service_details(last_odometer)

    raw_record = {
        "license_plate": user["license_plate"],
        "car_model": car_model,
        "car_condition": user["car_condition"],
        "coe_expiry_date": user["coe_expiry_date"],
        "part_name": part_name,
        "check_date": datetime.date.today().isoformat(),   # stored as YYYY-MM-DD
    }
    raw_record.update(details)    # last service date and km, current odometer
    raw_record.update(interval)   # interval_km, interval_months

    # Same plate, part, service date, service km and current km as before? Reuse
    # the saved answer instead of calling the AI again (saves API calls, same result).
    previous = data_manager.find_repeat(raw_record)
    if previous is not None:
        io_manager.display_repeated(previous)
        return

    io_manager.display_message("\nAsking the AI to check this part against your manual...")
    final_record = build_record(raw_record, manual_path)

    # A failed save is warned about, but the result is still shown.
    saved_ok = data_manager.save(final_record)
    if not saved_ok:
        io_manager.display_message("Warning: the assessment could not be saved to disk.")
    io_manager.display_result(final_record)


def history_flow(user: dict) -> None:
    """Menu option 2: list every saved check for this user's license plate.
    Returns nothing; explains on screen if there are none yet."""
    my_records = data_manager.query(lambda r: r.get("license_plate") == user["license_plate"])
    if not my_records:
        # Explain why the list is empty so the screen doesn't look broken.
        io_manager.display_message(f"\nNo checks saved for {user['license_plate']} yet. "
                                   "Choose 1 to check a part -- it will appear here.")
        return
    io_manager.display_list(my_records)


def urgent_flow(user: dict) -> None:
    """Menu option 3: list this user's checks that need attention, highest risk first.
    Returns nothing."""
    # is_urgent(): outcome starts with URGENT, FLAGGED, DUE SOON, COE EXPIRED
    # or NEEDS MANUAL REVIEW.
    urgent_records = data_manager.query(
        lambda r: r.get("license_plate") == user["license_plate"] and logic_manager.is_urgent(r)
    )
    # Highest risk_score first; records without a score sort as 0.
    urgent_records.sort(key=lambda r: r.get("risk_score", 0), reverse=True)
    if not urgent_records:
        io_manager.display_message("\nNothing needs attention right now: none of your checks are "
                                   "urgent, flagged, overdue or waiting for manual review. "
                                   "(Choose 2 to see all your checks.)")
        return
    io_manager.display_list(urgent_records)


# ======================================================================
# ACCOUNTS: log in, and Create / Read / Update / Delete a profile ("CRUD")
# ======================================================================

def login_flow() -> dict | None:
    """Log in by license plate.
    Returns the user's profile, or None if no profile has that plate."""
    plate = io_manager.prompt_login()
    user = data_manager.find_user_by_plate(plate)
    if user is None:
        io_manager.display_message(f"No profile for {plate}. Choose 'Register new profile' first.")
        return None
    io_manager.display_message(f"\nWelcome back, {user['name']}!")
    return user


def register_flow() -> dict | None:
    """CREATE a new profile and log the user in.
    Returns the new profile, or None if the car list is missing or saving failed."""
    car_models = data_manager.list_car_models()
    if not car_models:
        io_manager.display_message("The car list (data/cars.json) is missing, so you can't register yet.")
        return None

    # The license plate is the account ID, so io_manager must refuse duplicates.
    taken_plates = {u.get("license_plate") for u in data_manager.load_users()}

    identity = io_manager.prompt_identity(taken_plates)
    car_model = io_manager.prompt_car_choice(car_models)

    profile = {
        "name": identity["name"],
        "license_plate": identity["license_plate"],
        "car_model": car_model,
        # Brand New / Used comes from cars.json, not from the user.
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
    """UPDATE the user's name or COE expiry date (plate and car are fixed).
    Returns the updated profile, or the unchanged one if saving failed."""
    updated = io_manager.prompt_updated_profile(user)
    if not data_manager.save_user(updated):
        io_manager.display_message("Could not save your changes. Please try again.")
        return user
    io_manager.display_message("\nProfile updated.")
    io_manager.display_user(updated)
    return updated


def delete_profile_flow(user: dict) -> dict | None:
    """DELETE the profile and all its saved checks, after a y/n confirmation.
    Returns None if deleted (logs the user out), else the same user."""
    plate = user["license_plate"]
    record_count = len(data_manager.query(lambda r: r.get("license_plate") == plate))

    question = f"Permanently delete {plate} and its {record_count} maintenance record(s)?"
    if not io_manager.confirm_action(question):
        io_manager.display_message("Delete cancelled.")
        return user

    # Only remove the car's checks once the profile itself is gone.
    if not data_manager.delete_user(plate):
        io_manager.display_message("Could not delete the profile. Please try again.")
        return user
    data_manager.delete_records_for_plate(plate)
    io_manager.display_message("Profile and records deleted. You have been logged out.")
    return None


# ======================================================================
# THE MAIN LOOP: show the menu, do what the user picks, repeat
# ======================================================================

def hide_google_notices(record: logging.LogRecord) -> bool:
    """Log filter: hide Google AI library warnings that clutter the screen.
    Returns False (hide) only for google_genai messages below ERROR level."""
    from_google = record.name.startswith("google_genai")
    is_minor = record.levelno < logging.ERROR
    return not (from_google and is_minor)


def main() -> None:
    """Run the app: set up logging, then show the menu and handle choices until Exit.
    Returns nothing."""
    # Only io_manager may print(), so the other managers report problems via logging.
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(hide_google_notices)

    # The logged-in profile is passed to each flow; no global variables.
    current_user = None   # None = logged out

    try:
        while True:
            choice = io_manager.prompt_main_menu(current_user)

            if current_user is None:
                # ---------- Logged-out menu ----------
                if choice == "1":
                    current_user = login_flow()
                elif choice == "2":
                    current_user = register_flow()
                elif choice == "3":
                    io_manager.display_message("Goodbye!")
                    break
                else:
                    io_manager.display_message("Invalid choice. Please enter a number from the menu.")
            else:
                # ---------- Logged-in menu ----------
                # Options 1-4 pause so results aren't pushed off screen by the menu.
                if choice == "1":
                    check_part_flow(current_user)
                    io_manager.wait_for_enter()
                elif choice == "2":
                    history_flow(current_user)
                    io_manager.wait_for_enter()
                elif choice == "3":
                    urgent_flow(current_user)
                    io_manager.wait_for_enter()
                elif choice == "4":
                    io_manager.display_user(current_user)
                    io_manager.wait_for_enter()
                elif choice == "5":
                    current_user = update_profile_flow(current_user)
                elif choice == "6":
                    current_user = delete_profile_flow(current_user)   # None = logged out
                elif choice == "7":
                    io_manager.display_message(f"Logged out {current_user['name']}.")
                    current_user = None
                elif choice == "8":
                    io_manager.display_message("Goodbye!")
                    break
                else:
                    io_manager.display_message("Invalid choice. Please enter a number from the menu.")

    except (KeyboardInterrupt, EOFError):
        # Ctrl+C, or no keyboard attached (e.g. Docker run without -it): exit politely.
        io_manager.display_message("\nExiting. (If you're using Docker, run with: docker run -it ...)")


# Start the app only when run directly, not when the tests import this file.
if __name__ == "__main__":
    main()
