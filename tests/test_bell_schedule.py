import copy
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import main
from app.routers import bell_schedule


class FakeCursor:
    def __init__(self, data):
        self._data = data

    async def to_list(self, *_):
        return copy.deepcopy(self._data)


class FakeCollection:
    def __init__(self, data):
        self.data = data

    async def create_index(self, *_, **__):
        return None

    def find(self):
        return FakeCursor(self.data)

    async def update_one(self, query, update):
        group_name = query.get("group_name")
        for doc in self.data:
            if doc.get("group_name") == group_name:
                doc.update(update.get("$set", {}))
                return
        raise ValueError("Group not found")


def build_fake_db(schedules):
    return types.SimpleNamespace(
        schedules=FakeCollection(copy.deepcopy(schedules)),
        users=FakeCollection([]),
        client=types.SimpleNamespace(close=lambda: None),
    )


def build_schedule(group_name, building):
    return {
        "group_name": group_name,
        "shift_info": {"shift": 1, "building": building},
        "schedule": {
            "days": {
                "Понедельник": {
                    "1": {"subject": "Test"}
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_update_all_schedules_respects_building_specific_times(monkeypatch):
    schedules = [
        build_schedule("A-1", 1),
        build_schedule("B-1", 2),
        build_schedule("C-1", None),
    ]

    fake_db = types.SimpleNamespace(schedules=FakeCollection(schedules))
    monkeypatch.setattr(bell_schedule, "db", fake_db)

    bell_data = {
        "понедельник": {
            "1": {"1_shift": {"1": "09:00-09:45"}},
            "2": {"1_shift": {"1": "10:00-10:45"}},
            "default": {"1_shift": {"1": "08:00-08:45"}},
        }
    }

    updated = await bell_schedule._update_all_schedules(bell_data)

    assert updated == 3

    assert fake_db.schedules.data[0]["schedule"]["days"]["Понедельник"]["1"]["time"] == "09:00-09:45"
    assert fake_db.schedules.data[1]["schedule"]["days"]["Понедельник"]["1"]["time"] == "10:00-10:45"
    assert fake_db.schedules.data[2]["schedule"]["days"]["Понедельник"]["1"]["time"] == "08:00-08:45"


@pytest.mark.asyncio
async def test_update_all_schedules_falls_back_to_default_building(monkeypatch):
    schedules = [build_schedule("D-1", 3)]
    fake_db = types.SimpleNamespace(schedules=FakeCollection(schedules))
    monkeypatch.setattr(bell_schedule, "db", fake_db)

    bell_data = {
        "понедельник": {
            "default": {"1_shift": {"1": "11:00-11:45"}},
        }
    }

    await bell_schedule._update_all_schedules(bell_data)

    assert fake_db.schedules.data[0]["schedule"]["days"]["Понедельник"]["1"]["time"] == "11:00-11:45"


def create_test_client(monkeypatch, fake_db, main_file, override_file):
    monkeypatch.setattr(bell_schedule, "db", fake_db)
    monkeypatch.setattr(main, "db", fake_db)
    monkeypatch.setattr(bell_schedule, "MAIN_BELL_FILE", str(main_file))
    monkeypatch.setattr(bell_schedule, "OVERRIDE_FILE", str(override_file))
    return TestClient(main.app)


def test_upload_bell_schedule_saves_file_and_updates_schedules(monkeypatch, tmp_path):
    schedules = [
        {
            "group_name": "A-1",
            "shift_info": {"shift": 1, "building": None},
            "schedule": {"days": {"Понедельник": {"1": {"subject": "Math"}}}},
        }
    ]
    fake_db = build_fake_db(schedules)
    main_file = tmp_path / "main.json"
    override_file = tmp_path / "override.json"
    client = create_test_client(monkeypatch, fake_db, main_file, override_file)

    bell_payload = {"понедельник": {"default": {"1_shift": {"1": "08:00-08:45"}}}}

    response = client.post(
        "/bell_schedule/upload",
        files={"file": ("bells.json", json.dumps(bell_payload), "application/json")},
    )

    assert response.status_code == 200
    assert json.loads(main_file.read_text(encoding="utf-8")) == bell_schedule._normalize_bell_data(bell_payload)
    assert (
        fake_db.schedules.data[0]["schedule"]["days"]["Понедельник"]["1"]["time"]
        == "08:00-08:45"
    )


@pytest.mark.parametrize(
    "endpoint",
    ["/bell_schedule/upload", "/bell_schedule/upload/special"],
)
def test_upload_rejects_non_json_files(monkeypatch, tmp_path, endpoint):
    fake_db = build_fake_db([])
    main_file = tmp_path / "main.json"
    override_file = tmp_path / "override.json"
    client = create_test_client(monkeypatch, fake_db, main_file, override_file)

    response = client.post(
        endpoint,
        files={"file": ("bells.txt", "not json", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Нужен JSON-файл"


def test_upload_special_updates_only_selected_days(monkeypatch, tmp_path):
    schedules = [
        {
            "group_name": "B-1",
            "shift_info": {"shift": 1, "building": 1},
            "schedule": {
                "days": {
                    "Понедельник": {"1": {"subject": "History", "time": "unchanged"}},
                    "Вторник": {"1": {"subject": "Science"}},
                }
            },
        }
    ]
    fake_db = build_fake_db(schedules)
    main_file = tmp_path / "main.json"
    override_file = tmp_path / "override.json"
    client = create_test_client(monkeypatch, fake_db, main_file, override_file)

    override_payload = {"понедельник": {"1": {"1_shift": {"1": "10:00-10:45"}}}}

    response = client.post(
        "/bell_schedule/upload/special",
        files={"file": ("override.json", json.dumps(override_payload), "application/json")},
    )

    assert response.status_code == 200
    assert json.loads(override_file.read_text(encoding="utf-8")) == bell_schedule._normalize_bell_data(override_payload)
    assert (
        fake_db.schedules.data[0]["schedule"]["days"]["Понедельник"]["1"]["time"]
        == "10:00-10:45"
    )
    assert (
        "time" not in fake_db.schedules.data[0]["schedule"]["days"]["Вторник"]["1"]
    )
