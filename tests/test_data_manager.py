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
    saved = (data_manager.DATA_FILE, data_manager.USERS_FILE, data_manager.CARS_FILE)
    folder = tempfile.mkdtemp()
    data_manager.DATA_FILE = os.path.join(folder, "maintenance_records.json")
    data_manager.USERS_FILE = os.path.join(folder, "users.json")
    data_manager.CARS_FILE = os.path.join(folder, "cars.json")
    try:
        yield folder
    finally:
        data_manager.DATA_FILE, data_manager.USERS_FILE, data_manager.CARS_FILE = saved
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
        data_manager.save({"license_plate": "SGX88B", "part_name": "Battery", "current_odometer": 9_000})
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
        data_manager.save({"license_plate": "SBA1234A", "part_name": "Battery", "current_odometer": 42_500})
        data_manager.save({"license_plate": "SGX88B", "part_name": "Battery", "current_odometer": 90_000})
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


# --- Car catalogue (the 4 supported cars) ---------------------------------------

CATALOGUE = {"cars": [
    {"model": "Honda Civic 2025", "condition": "Brand New", "manual": "manuals/honda.pdf",
     "schedule": {"Engine Oil": {"km": 10000, "months": 12}, "Brake Pads": {"km": 40000, "months": 24}}},
    {"model": "Kia Forte", "condition": "Used", "manual": "manuals/kia.pdf",
     "schedule": {"Engine Oil": {"km": 8000, "months": 6}}},
]}


def test_lists_models_and_finds_condition():
    with temp_data_files():
        write(data_manager.CARS_FILE, json.dumps(CATALOGUE))
        assert data_manager.list_car_models() == ["Honda Civic 2025", "Kia Forte"]
        assert data_manager.find_car("Kia Forte")["condition"] == "Used"
        assert data_manager.find_car("Toyota Corolla") is None


def test_each_car_has_its_own_parts_and_intervals():
    with temp_data_files():
        write(data_manager.CARS_FILE, json.dumps(CATALOGUE))
        assert data_manager.list_parts("Honda Civic 2025") == ["Brake Pads", "Engine Oil"]
        assert data_manager.list_parts("Kia Forte") == ["Engine Oil"]
        assert data_manager.get_service_interval("Kia Forte", "Engine Oil") == \
            {"interval_km": 8000, "interval_months": 6}
        assert data_manager.get_service_interval("Kia Forte", "Brake Pads") is None


def test_missing_or_corrupt_catalogue_returns_empty():
    with temp_data_files():
        assert data_manager.list_car_models() == []       # missing
        write(data_manager.CARS_FILE, "garbage")
        assert data_manager.list_car_models() == []       # corrupt
        assert data_manager.list_parts("Kia Forte") == []


def test_invalid_entries_are_skipped():
    with temp_data_files():
        bad = {"cars": [
            {"model": "Kia Forte", "condition": "Old", "manual": "x.pdf", "schedule": {}},  # bad condition
            {"model": "Honda Civic 2025", "condition": "Brand New", "manual": "h.pdf",
             "schedule": {"Engine Oil": {"km": 10000, "months": 12}, "Tyres": {"km": "lots"}}},
        ]}
        write(data_manager.CARS_FILE, json.dumps(bad))
        assert data_manager.list_car_models() == ["Honda Civic 2025"]
        assert data_manager.list_parts("Honda Civic 2025") == ["Engine Oil"]


def test_manual_path_only_when_file_exists():
    with temp_data_files():
        write(data_manager.CARS_FILE, json.dumps(CATALOGUE))
        assert data_manager.get_manual_path("Kia Forte") is None  # PDF not there
        saved = data_manager.PROJECT_DIR
        data_manager.PROJECT_DIR = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(data_manager.PROJECT_DIR, "manuals"))
            write(os.path.join(data_manager.PROJECT_DIR, "manuals", "kia.pdf"), "%PDF-1.4")
            assert data_manager.get_manual_path("Kia Forte").endswith("manuals/kia.pdf")
        finally:
            shutil.rmtree(data_manager.PROJECT_DIR, ignore_errors=True)
            data_manager.PROJECT_DIR = saved


def test_real_catalogue_file_is_valid():
    # Guards the committed data/cars.json against typos.
    assert data_manager.list_car_models() == [
        "Honda Civic 2025", "Mazda 3 Hatchback 2020", "Hyundai Elantra 2021", "Kia Forte"]
    for model in data_manager.list_car_models():
        assert len(data_manager.list_parts(model)) >= 1, model


def test_last_odometer_skips_damaged_entries():
    records = [{"license_plate": "SBA1234A", "current_odometer": 40_000},
               {"license_plate": "SBA1234A", "current_odometer": "52000"},  # text
               {"license_plate": "SBA1234A"}]                               # missing
    assert data_manager.get_last_odometer(records, "SBA1234A") == 40_000


# --- Repeated checks are detected (no second AI call) -------------------------------

CHECK = {"license_plate": "SBA1234A", "part_name": "Engine Oil", "last_service_date": "2025-09-03",
         "last_service_mileage": 40_000, "current_odometer": 49_000}


def test_identical_check_is_found_as_repeat():
    with temp_data_files():
        data_manager.save({**CHECK, "check_date": "2026-10-01", "urgency_score": 3})
        previous = data_manager.find_repeat({**CHECK, "check_date": "2026-10-09"})
        assert previous is not None
        assert previous["urgency_score"] == 3


def test_any_changed_input_is_not_a_repeat():
    with temp_data_files():
        data_manager.save(CHECK)
        for field, new_value in [("current_odometer", 49_500), ("part_name", "Battery"),
                                 ("last_service_date", "2025-09-04"), ("license_plate", "SGX88B"),
                                 ("last_service_mileage", 40_001)]:
            assert data_manager.find_repeat({**CHECK, field: new_value}) is None, field


def test_no_history_means_no_repeat():
    with temp_data_files():
        assert data_manager.find_repeat(CHECK) is None