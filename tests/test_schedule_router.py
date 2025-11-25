import io
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import main
from app.services import schedule_service
from app.services.schedule_service import ScheduleService


class DummyCollection:
    async def create_index(self, *_, **__):
        return None


def create_test_client(monkeypatch: pytest.MonkeyPatch):
    fake_db = types.SimpleNamespace(
        schedules=DummyCollection(),
        users=DummyCollection(),
        client=types.SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(main, "db", fake_db)
    monkeypatch.setattr(schedule_service, "db", fake_db)
    return TestClient(main.app)


def test_get_all_schedules(monkeypatch):
    client = create_test_client(monkeypatch)
    expected_payload = [
        {
            "group_name": "A-1",
            "shift_info": {"shift": 1, "room": None, "building": None},
            "schedule": {"zero_lesson": {}, "days": {}},
            "updated_at": None,
        }
    ]

    async def fake_get_all():
        return expected_payload

    monkeypatch.setattr(ScheduleService, "get_all_schedules", staticmethod(fake_get_all))

    response = client.get("/schedule/?day=Monday")

    assert response.status_code == 200
    assert response.json() == expected_payload


def test_get_schedule_by_group(monkeypatch):
    client = create_test_client(monkeypatch)
    captured = {}

    async def fake_get_schedule(group_name: str, day: str | None):
        captured["args"] = (group_name, day)
        return {
            "group_name": group_name,
            "day": day,
            "schedule": {"days": {day: {"1": {"subject": "Math"}}}},
            "shift_info": {"shift": 1},
            "updated_at": None,
        }

    monkeypatch.setattr(ScheduleService, "get_schedule_by_group", staticmethod(fake_get_schedule))

    response = client.get("/schedule/TEST-1", params={"day": "monday"})

    assert response.status_code == 200
    assert captured["args"] == ("TEST-1", "monday")
    assert response.json()["schedule"]["days"]["monday"]["1"]["subject"] == "Math"


def test_get_schedule_not_found(monkeypatch):
    client = create_test_client(monkeypatch)

    async def fake_get_schedule(group_name: str, day: str | None):
        raise HTTPException(status_code=404, detail="Group missing")

    monkeypatch.setattr(ScheduleService, "get_schedule_by_group", staticmethod(fake_get_schedule))

    response = client.get("/schedule/UNKNOWN", params={"day": "Mon"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Group missing"


def test_get_teacher_schedule(monkeypatch):
    client = create_test_client(monkeypatch)
    captured = {}

    async def fake_get_teacher(fio: str, day: str | None):
        captured["args"] = (fio, day)
        return {
            "teacher_fio": fio,
            "filtered_by_day": day,
            "schedule": {
                "first_shift": {
                    day: {
                        "1": {
                            "subject": "CS",
                            "group": "A-1",
                            "classroom": None,
                            "time": None,
                        }
                    }
                },
                "second_shift": {},
            },
        }

    monkeypatch.setattr(ScheduleService, "get_teacher_schedule", staticmethod(fake_get_teacher))

    response = client.get("/schedule/teacher/Ivanov", params={"day": "Tuesday"})

    assert response.status_code == 200
    assert captured["args"] == ("Ivanov", "Tuesday")
    assert response.json()["schedule"]["first_shift"]["Tuesday"]["1"]["group"] == "A-1"


def test_get_teacher_schedule_missing(monkeypatch):
    client = create_test_client(monkeypatch)

    async def fake_get_teacher(fio: str, day: str | None):
        raise HTTPException(status_code=404, detail="Teacher not found")

    monkeypatch.setattr(ScheduleService, "get_teacher_schedule", staticmethod(fake_get_teacher))

    response = client.get("/schedule/teacher/Unknown", params={"day": "Fri"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Teacher not found"


def test_get_teacher_schedule_invalid_name(monkeypatch):
    client = create_test_client(monkeypatch)

    async def fake_get_teacher(fio: str, day: str | None):
        raise HTTPException(status_code=400, detail="Invalid teacher")

    monkeypatch.setattr(ScheduleService, "get_teacher_schedule", staticmethod(fake_get_teacher))

    response = client.get("/schedule/teacher/%20", params={"day": "Fri"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid teacher"


def test_upload_schedule_with_shifts(monkeypatch):
    client = create_test_client(monkeypatch)
    captured = {}

    async def fake_upload(schedule_file, shifts_file):
        if not schedule_file.filename.endswith(".docx"):
            raise HTTPException(status_code=400, detail="Нужен DOCX файл расписания")
        captured["schedule_bytes"] = await schedule_file.read()
        captured["shifts_bytes"] = await shifts_file.read() if shifts_file else None
        return {
            "message": "ok",
            "inserted_ids": ["1"],
            "total_groups": 1,
            "first_shift": 1,
            "second_shift": 0,
        }

    monkeypatch.setattr(ScheduleService, "upload_schedule", staticmethod(fake_upload))

    response = client.post(
        "/schedule/upload",
        files={
            "schedule_file": ("schedule.docx", io.BytesIO(b"docx content"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "shifts_file": ("shifts.json", io.BytesIO(b"{}"), "application/json"),
        },
    )

    assert response.status_code == 200
    assert captured["schedule_bytes"] == b"docx content"
    assert captured["shifts_bytes"] == b"{}"
    assert response.json()["message"] == "ok"


def test_upload_schedule_rejects_non_docx(monkeypatch):
    client = create_test_client(monkeypatch)

    async def fake_upload(schedule_file, shifts_file):
        if not schedule_file.filename.endswith(".docx"):
            raise HTTPException(status_code=400, detail="Нужен DOCX файл расписания")
        return {}

    monkeypatch.setattr(ScheduleService, "upload_schedule", staticmethod(fake_upload))

    response = client.post(
        "/schedule/upload",
        files={"schedule_file": ("schedule.txt", io.BytesIO(b"text"), "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Нужен DOCX файл расписания"


def test_delete_schedule_success(monkeypatch):
    client = create_test_client(monkeypatch)

    async def fake_delete(group_name: str):
        return {"message": f"Schedule for '{group_name}' deleted"}

    monkeypatch.setattr(ScheduleService, "delete_schedule", staticmethod(fake_delete))

    response = client.delete("/schedule/CS-101")

    assert response.status_code == 200
    assert response.json()["message"] == "Schedule for 'CS-101' deleted"


def test_delete_schedule_not_found(monkeypatch):
    client = create_test_client(monkeypatch)

    async def fake_delete(group_name: str):
        raise HTTPException(status_code=404, detail="Schedule not found")

    monkeypatch.setattr(ScheduleService, "delete_schedule", staticmethod(fake_delete))

    response = client.delete("/schedule/UNKNOWN")

    assert response.status_code == 404
    assert response.json()["detail"] == "Schedule not found"
