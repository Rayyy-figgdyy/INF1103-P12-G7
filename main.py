"""
main.py -- entry point. Wires the four managers into the pipeline:

    User -> io_manager -> ai_manager -> logic_manager -> data_manager

This is the only module that imports more than one manager. The managers
never import each other; main.py passes data between them.

Login state is kept in the local variable `current_user` inside main()
and passed into / returned from each flow -- no globals, no classes.
"""
from __future__ import annotations

import datetime
import logging

import ai_manager
import data_manager
import io_manager
import logic_manager


# ======================================================================
# The pipeline
# ======================================================================

def build_record(raw_record: dict) -> dict:
    """AI enrichment, then business rules. Returns the final record."""
    enriched = ai_manager.process(raw_record)
    final_record = {**enriched, **logic_manager.evaluate(enriched)}
    final_record["risk_score"] = logic_manager.score(final_record)
    final_record["outcome"] = logic_manager.route(final_record)
    return final_record


def check_part_flow(user: dict) -> dict:
    parts = data_manager.list_parts(user["car_model"])
    if not parts:
        io_manager.display_message("No service schedule is available, so parts can't be checked.")
        return user

    part_name = io_manager.prompt_part_choice(parts)
    interval = data_manager.get_service_interval(user["car_model"], part_name)
    if interval is None:
        io_manager.display_message(f"No stored service interval for {part_name}.")
        return user
    last_odometer = data_manager.get_last_odometer(data_manager.load(), user["license_plate"])
    details = io_manager.prompt_service_details(last_odometer)

    raw_record = {
        "license_plate": user["license_plate"],
        "car_model": user["car_model"],
        "car_condition": user["car_condition"],
        "coe_expiry_date": user["coe_expiry_date"],
        "part_name": part_name,
        **details,
        **interval,
        "check_date": datetime.date.today().isoformat(),
    }

    io_manager.display_message("\nAsking the AI to assess this part...")
    final_record = build_record(raw_record)

    if not data_manager.save(final_record):
        io_manager.display_message("Warning: the assessment could not be saved to disk.")
    io_manager.display_result(final_record)
    return user


def history_flow(user: dict) -> dict:
    records = data_manager.query(lambda r: r.get("license_plate") == user["license_plate"])
    io_manager.display_list(records)
    return user


def urgent_flow(user: dict) -> dict:
    records = data_manager.query(
        lambda r: r.get("license_plate") == user["license_plate"] and logic_manager.is_urgent(r)
    )
    records.sort(key=lambda r: r.get("risk_score", 0), reverse=True)
    io_manager.display_list(records)
    return user


# ======================================================================
# Account: login + profile CRUD
# ======================================================================

def login_flow(user: dict | None) -> dict | None:
    plate = io_manager.prompt_login()
    found = data_manager.find_user_by_plate(plate)
    if found is None:
        io_manager.display_message(f"No profile for {plate}. Choose 'Register new profile' first.")
        return None
    io_manager.display_message(f"\nWelcome back, {found['name']}!")
    return found


def _choose_car_model() -> str:
    """Car model by text, or identified from a photo by the AI."""
    if io_manager.prompt_car_model_source() == "photo":
        path = io_manager.prompt_photo_path()
        io_manager.display_message("Identifying the car from your photo...")
        car = ai_manager.identify_car_model(path)
        if car is None:
            io_manager.display_message("Couldn't identify the car from that photo -- please type the model instead.")
        elif io_manager.confirm_identified_car(car):
            return f"{car['make']} {car['model']} {car['year_range']}"
    return io_manager.prompt_car_model_text()


def register_flow(user: dict | None) -> dict | None:
    taken_plates = {u.get("license_plate") for u in data_manager.load_users()}
    profile = {
        **io_manager.prompt_identity(taken_plates),
        "car_model": _choose_car_model(),
        **io_manager.prompt_car_details(),
    }
    if not data_manager.save_user(profile):
        io_manager.display_message("Could not save your profile. Please try again.")
        return None
    io_manager.display_message("\nProfile created -- you are now logged in.")
    io_manager.display_user(profile)
    return profile


def view_profile_flow(user: dict) -> dict:
    io_manager.display_user(user)
    return user


def update_profile_flow(user: dict) -> dict:
    updated = io_manager.prompt_updated_profile(user)
    if not data_manager.save_user(updated):
        io_manager.display_message("Could not save your changes. Please try again.")
        return user
    io_manager.display_message("\nProfile updated.")
    io_manager.display_user(updated)
    return updated


def delete_profile_flow(user: dict) -> dict | None:
    plate = user["license_plate"]
    count = len(data_manager.query(lambda r: r.get("license_plate") == plate))
    if not io_manager.confirm_action(
        f"Permanently delete {plate} and its {count} maintenance record(s)?"
    ):
        io_manager.display_message("Delete cancelled.")
        return user
    if not data_manager.delete_user(plate):
        io_manager.display_message("Could not delete the profile. Please try again.")
        return user
    data_manager.delete_records_for_plate(plate)
    io_manager.display_message("Profile and records deleted. You have been logged out.")
    return None


def logout_flow(user: dict) -> None:
    io_manager.display_message(f"Logged out {user['name']}.")
    return None


# ======================================================================
# Main loop
# ======================================================================

LOGGED_OUT_ACTIONS = {"1": login_flow, "2": register_flow}
LOGGED_OUT_EXIT = "3"

LOGGED_IN_ACTIONS = {
    "1": check_part_flow,
    "2": history_flow,
    "3": urgent_flow,
    "4": view_profile_flow,
    "5": update_profile_flow,
    "6": delete_profile_flow,
    "7": logout_flow,
}
LOGGED_IN_EXIT = "8"


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
    current_user: dict | None = None
    try:
        while True:
            choice = io_manager.prompt_main_menu(current_user)
            if current_user is None:
                actions, exit_choice = LOGGED_OUT_ACTIONS, LOGGED_OUT_EXIT
            else:
                actions, exit_choice = LOGGED_IN_ACTIONS, LOGGED_IN_EXIT

            if choice == exit_choice:
                io_manager.display_message("Goodbye!")
                return
            action = actions.get(choice)
            if action is None:
                io_manager.display_message("Invalid choice. Please enter a number from the menu.")
                continue
            current_user = action(current_user)
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C, or no interactive terminal (e.g. `docker run` without -it).
        io_manager.display_message("\nExiting. (If you're using Docker, run with: docker run -it ...)")


if __name__ == "__main__":
    main()
