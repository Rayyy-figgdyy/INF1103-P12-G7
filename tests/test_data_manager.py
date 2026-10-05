"""
Tests for data_manager: user CRUD, maintenance records, the stored
manufacturer schedule, and missing/corrupt file handling. Every test
uses a temporary folder, so real data in data/ is never touched.
"""
import contextlib
import json
import os
import shutil
import tempfile

import data_manager

USER = {
    "name": "Darren",
    "license_plate": "SBA1234A",
    "car_model": "Toyota Corolla 2020",
    "car_condition": "Used",
    "coe_expiry_date": "2030-06-30",
}


@contextlib.contextmanager
def temp_data_files():
    """Point data_manager at empty temp files for the duration of a test."""
    saved = (data_manager.DATA_FILE, data_manager.USERS_FILE, data_manager.SCHEDULE_FILE)
    folder = tempfile.mkdtemp()
    data_manager.DATA_FILE = os.path.join(folder, "maintenance_records.json")
    data_manager.USERS_FILE = os.path.join(folder, "users.json")
    data_manager.SCHEDULE_FILE = os.path.join(folder, "service_schedule.json")
    try:
        yield folder
    finally:
        data_manager.DATA_FILE, data_manager.USERS_FILE, data_manager.SCHEDULE_FILE = saved
        shutil.rmtree(folder, ignore_errors=True)


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# --- Missing / corrupt files ------------------------------------------------

def test_missing_files_return_empty():
    with temp_data_files():
        assert data_manager.load() == []
        assert data_manager.load_users() == []
        assert data_manager.find_user_by_plate("SBA1234A") is None


def test_corrupt_files_return_empty_instead_of_crashing():
    with temp_data_files():
        write(data_manager.DATA_FILE, "{this is not json")
        write(data_manager.USERS_FILE, '{"not": "a list"}')
        assert data_manager.load() == []
        assert data_manager.load_users() == []


# --- User CRUD ------------------------------------------------------------------

def test_create_and_read_user():
    with temp_data_files():
        assert data_manager.save_user(USER) is True
        assert data_manager.find_user_by_plate("SBA1234A")["name"] == "Darren"


def test_update_replaces_instead_of_duplicating():
    with temp_data_files():
        data_manager.save_user(USER)
        data_manager.save_user({**USER, "car_model": "Honda Civic 2022"})
        users = data_manager.load_users()
        assert len(users) == 1
        assert users[0]["car_model"] == "Honda Civic 2022"


def test_delete_user():
    with temp_data_files():
        data_manager.save_user(USER)
        data_manager.save_user({**USER, "license_plate": "SGX88B", "name": "Kai"})
        assert data_manager.delete_user("SBA1234A") is True
        assert data_manager.find_user_by_plate("SBA1234A") is None
        assert data_manager.find_user_by_plate("SGX88B") is not None
        assert data_manager.delete_user("SBA1234A") is False  # already gone


# --- Maintenance records ------------------------------------------------------------

def test_save_load_and_query_records():
    with temp_data_files():
        data_manager.save({"license_plate": "SBA1234A", "part_name": "Engine Oil", "current_odometer": 40_000})
        data_manager.save({"license_plate": "SGX88B", "part_name": "Tyres", "current_odometer": 9_000})
        assert len(data_manager.load()) == 2
        mine = data_manager.query(lambda r: r["license_plate"] == "SBA1234A")
        assert [r["part_name"] for r in mine] == ["Engine Oil"]


def test_persists_across_separate_loads():
    # Same data on the next run: everything is re-read from the file.
    with temp_data_files():
        data_manager.save({"license_plate": "SBA1234A", "current_odometer": 1})
        first, second = data_manager.load(), data_manager.load()
        assert first == second == [{"license_plate": "SBA1234A", "current_odometer": 1}]


def test_last_odometer_is_per_car_across_all_parts():
    with temp_data_files():
        data_manager.save({"license_plate": "SBA1234A", "part_name": "Engine Oil", "current_odometer": 40_000})
        data_manager.save({"license_plate": "SBA1234A", "part_name": "Tyres", "current_odometer": 42_500})
        data_manager.save({"license_plate": "SGX88B", "part_name": "Tyres", "current_odometer": 90_000})
        records = data_manager.load()
        assert data_manager.get_last_odometer(records, "SBA1234A") == 42_500
        assert data_manager.get_last_odometer(records, "NEW1A") is None


def test_delete_records_for_plate():
    with temp_data_files():
        data_manager.save({"license_plate": "SBA1234A"})
        data_manager.save({"license_plate": "SBA1234A"})
        data_manager.save({"license_plate": "SGX88B"})
        assert data_manager.delete_records_for_plate("SBA1234A") == 2
        assert data_manager.load() == [{"license_plate": "SGX88B"}]


# --- Stored manufacturer schedule (business rule 2) ---------------------------------

SCHEDULE = {
    "default": {"Engine Oil": {"km": 10000, "months": 6}, "Spark Plugs": {"km": 30000, "months": 24}},
    "models": {"toyota corolla": {"Spark Plugs": {"km": 100000, "months": 72}}},
}


def test_interval_uses_model_override_when_model_matches():
    with temp_data_files():
        write(data_manager.SCHEDULE_FILE, json.dumps(SCHEDULE))
        assert data_manager.get_service_interval("Toyota Corolla Altis 2021", "Spark Plugs") == \
            {"interval_km": 100000, "interval_months": 72}
        assert data_manager.get_service_interval("Honda Jazz", "Spark Plugs") == \
            {"interval_km": 30000, "interval_months": 24}


def test_unknown_part_has_no_interval():
    with temp_data_files():
        write(data_manager.SCHEDULE_FILE, json.dumps(SCHEDULE))
        assert data_manager.get_service_interval("Honda Jazz", "Flux Capacitor") is None
        assert data_manager.list_parts("Honda Jazz") == ["Engine Oil", "Spark Plugs"]


def test_missing_or_corrupt_schedule_falls_back_to_built_in():
    with temp_data_files():
        assert "Engine Oil" in data_manager.list_parts("Any Car")  # missing file
        write(data_manager.SCHEDULE_FILE, "garbage")
        assert "Engine Oil" in data_manager.list_parts("Any Car")  # corrupt file


def test_invalid_schedule_entries_are_skipped():
    with temp_data_files():
        bad = {"default": {"Engine Oil": {"km": 10000, "months": 6}, "Tyres": {"km": "lots"}}, "models": {}}
        write(data_manager.SCHEDULE_FILE, json.dumps(bad))
        assert data_manager.list_parts("Any Car") == ["Engine Oil"]


def test_real_schedule_file_is_valid():
    # Guards the committed data/service_schedule.json against typos.
    assert len(data_manager.list_parts("Toyota Corolla 2020")) >= 5
