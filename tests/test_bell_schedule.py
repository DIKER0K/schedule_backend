import copy
import sys
import types
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.routers import bell_schedule


class FakeCursor:
    def __init__(self, data):
        self._data = data

    async def to_list(self, *_):
        return copy.deepcopy(self._data)


class FakeCollection:
    def __init__(self, data):
        self.data = data

    def find(self):
        return FakeCursor(self.data)

    async def update_one(self, query, update):
        group_name = query.get("group_name")
        for doc in self.data:
            if doc.get("group_name") == group_name:
                doc.update(update.get("$set", {}))
                return
        raise ValueError("Group not found")


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
