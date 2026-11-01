"""Проводка эндпоинтов: параметр `date` в выдаче и поля загрузки."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    # Без контекстного менеджера lifespan не запускается: индексы и прод-базу
    # не трогаем, работаем с подменённой коллекцией (fixture fake_db).
    return TestClient(app)


def schedule_only_thursday(valid_from="05.11.2026"):
    return {
        "9-1": {
            "title": f"Расписание уроков для 9-1 группы с {valid_from} г.",
            "lessons": {"1": {"Четверг": "История Кузнецов К.К. 104"}},
        }
    }


def test_schedule_by_date_returns_version(fake_db, workdir, make_docx, client):
    client.post(
        "/schedule/upload",
        files={"schedule_file": ("v1.docx", make_docx("v1.docx", schedule_only_thursday()).read_bytes())},
    )

    response = client.get("/schedule/9-1", params={"date": "2026-11-05"})

    assert response.status_code == 200
    body = response.json()
    assert body["valid_from"] == "2026-11-05"
    assert body["requested_date"] == "2026-11-05"
    assert body["schedule"]["days"]["Четверг"]["1"]["subject"] == "История"


def test_schedule_before_version_is_404(fake_db, workdir, make_docx, client):
    client.post(
        "/schedule/upload",
        files={"schedule_file": ("v1.docx", make_docx("v1.docx", schedule_only_thursday()).read_bytes())},
    )

    response = client.get("/schedule/9-1", params={"date": "2026-11-01"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Расписание на 01.11.2026 ещё не опубликовано"


def test_schedule_bad_date_is_422(fake_db, client):
    response = client.get("/schedule/9-1", params={"date": "вчера"})

    assert response.status_code == 422


def test_upload_accepts_valid_from_as_form_field(fake_db, workdir, make_docx, client):
    groups = schedule_only_thursday()
    groups["9-1"]["title"] = "Расписание уроков для 9-1 группы"
    path = make_docx("v1.docx", groups)

    response = client.post(
        "/schedule/upload",
        files={"schedule_file": ("v1.docx", path.read_bytes())},
        data={"valid_from": "2026-11-02"},
    )

    assert response.status_code == 200
    assert response.json()["valid_from"] == "2026-11-02"
    assert response.json()["dates"] == {"9-1": "2026-11-02"}


def test_upload_accepts_valid_from_as_query_param(fake_db, workdir, make_docx, client):
    groups = schedule_only_thursday()
    groups["9-1"]["title"] = "Расписание уроков для 9-1 группы"
    path = make_docx("v1.docx", groups)

    response = client.post(
        "/schedule/upload?valid_from=2026-11-03",
        files={"schedule_file": ("v1.docx", path.read_bytes())},
    )

    assert response.status_code == 200
    assert response.json()["dates"] == {"9-1": "2026-11-03"}


def test_upload_without_date_is_400(fake_db, workdir, make_docx, client):
    groups = schedule_only_thursday()
    groups["9-1"]["title"] = "Расписание уроков для 9-1 группы"
    path = make_docx("v1.docx", groups)

    response = client.post(
        "/schedule/upload",
        files={"schedule_file": ("v1.docx", path.read_bytes())},
    )

    assert response.status_code == 400
    assert "укажите valid_from" in response.json()["detail"]
