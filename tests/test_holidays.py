"""Календарь нерабочих дней: API, выдача звонков и расписания.

Приказ «4 ноября — выходной» — это не перенос звонков, а отсутствие занятий:
бот должен сказать про выходной, «следующий учебный день» — перепрыгнуть
праздник, а расписание группы на эту дату — не отдаваться вовсе.
"""

import asyncio
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services.holidays_service import (
    INDEX_NAME,
    add_holiday,
    ensure_holiday_indexes,
    fmt_day_month,
    holiday_on,
    holidays_between,
    list_holidays,
    remove_holiday,
    seed_holidays,
)

HOLIDAY = {"date": "2026-11-04", "title": "День народного единства"}


@pytest.fixture
def client():
    # Без контекстного менеджера lifespan не запускается: индексы и прод-базу
    # не трогаем, работаем с подменённой коллекцией (fixture fake_db).
    return TestClient(app)


@pytest.fixture
def holiday(fake_db):
    """4 ноября — выходной, как в приказе."""
    return asyncio.run(add_holiday(date(2026, 11, 4), HOLIDAY["title"]))


# --------------------------------------------------------------------------
# 1. API календаря
# --------------------------------------------------------------------------


def test_add_and_list_holiday(fake_db, client):
    created = client.post("/holidays", json=HOLIDAY)

    assert created.status_code == 201
    assert created.json() == HOLIDAY

    listed = client.get("/holidays", params={"year": 2026})
    assert listed.status_code == 200
    assert listed.json() == [HOLIDAY]

    assert client.get("/holidays", params={"year": 2027}).json() == []


def test_duplicate_date_is_409(fake_db, client):
    assert client.post("/holidays", json=HOLIDAY).status_code == 201

    duplicate = client.post("/holidays", json=HOLIDAY)

    assert duplicate.status_code == 409
    assert "уже отмечено выходным" in duplicate.json()["detail"]
    assert len(client.get("/holidays").json()) == 1


def test_add_holiday_needs_title(fake_db, client):
    response = client.post("/holidays", json={"date": "2026-11-04", "title": "  "})

    assert response.status_code == 422


def test_delete_holiday(fake_db, client):
    client.post("/holidays", json=HOLIDAY)

    removed = client.delete("/holidays/2026-11-04")

    assert removed.status_code == 200
    assert removed.json()["date"] == "2026-11-04"
    assert client.get("/holidays").json() == []
    assert client.delete("/holidays/2026-11-04").status_code == 404


def test_single_document_per_date(fake_db, holiday):
    """Уникальный индекс не даёт завести две записи на одну дату."""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_holiday(date(2026, 11, 4), "Другое название"))

    assert exc.value.status_code == 409


def test_holiday_index_is_created_at_startup(fake_db):
    asyncio.run(ensure_holiday_indexes())

    info = asyncio.run(fake_db.holidays.index_information())
    assert info[INDEX_NAME]["key"] == [("date", 1)]
    assert info[INDEX_NAME]["unique"] is True


def test_list_holidays_sorted_and_parsed(fake_db):
    asyncio.run(add_holiday(date(2026, 11, 5), "Второй"))
    asyncio.run(add_holiday(date(2026, 1, 7), "Первый"))

    listed = asyncio.run(list_holidays(2026))

    assert [item["date"] for item in listed] == [
        date(2026, 1, 7),
        date(2026, 11, 5),
    ]


def test_day_month_is_russian(fake_db):
    assert fmt_day_month(date(2026, 11, 4)) == "4 ноября"
    assert fmt_day_month(date(2027, 1, 1)) == "1 января"


def test_broken_manual_record_does_not_break_output(fake_db):
    """Запись с битой датой, заведённая руками, не роняет список и звонки."""
    asyncio.run(fake_db.holidays.insert_one({"date": "не дата", "title": "Мусор"}))
    asyncio.run(add_holiday(date(2026, 11, 4), HOLIDAY["title"]))

    assert [item["date"] for item in asyncio.run(list_holidays())] == [
        date(2026, 11, 4)
    ]
    assert asyncio.run(holidays_between(date(2026, 11, 1), date(2026, 11, 30))) == {
        "2026-11-04": HOLIDAY["title"]
    }


# --------------------------------------------------------------------------
# 2. Миграция списка учебного года
# --------------------------------------------------------------------------


def test_seed_migration_adds_holidays_once(fake_db):
    assert asyncio.run(seed_holidays()) == 1
    assert asyncio.run(holiday_on(date(2026, 11, 4)))["title"] == HOLIDAY["title"]

    # Повторный старт ничего не добавляет и не перезаписывает
    assert asyncio.run(seed_holidays()) == 0
    assert len(asyncio.run(list_holidays())) == 1


def test_seed_migration_does_not_resurrect_deleted_holiday(fake_db):
    """Выходной сняли через API (занятия перенесли) — перезапуск его не вернёт."""
    asyncio.run(seed_holidays())
    asyncio.run(remove_holiday(date(2026, 11, 4)))

    assert asyncio.run(seed_holidays()) == 0
    assert asyncio.run(holiday_on(date(2026, 11, 4))) is None


# --------------------------------------------------------------------------
# 3. Выдача звонков
# --------------------------------------------------------------------------


def test_next_bell_on_holiday_says_day_off(fake_db, workdir, holiday, client):
    response = client.get("/next-bell", params={"at": "2026-11-04T10:00"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_school_day"] is False
    assert body["current_event"]["type"] == "no_classes"
    assert body["current_event"]["label"] == "выходной"
    assert body["next_bell"] is None
    assert body["lessons"] == []
    assert body["message"].startswith(
        "🎉 4 ноября — выходной (День народного единства). Звонков нет."
    )
    assert "Следующий учебный день — четверг" in body["message"]


def test_next_school_day_skips_holiday(fake_db, workdir, holiday, client):
    """Вечером 3 ноября следующий учебный день — 5-е, а не выходное 4-е."""
    response = client.get("/next-bell", params={"at": "2026-11-03T18:00"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_school_day"] is True
    assert "занятия закончились" in body["message"]
    assert "Следующий учебный день — четверг" in body["message"]
    assert "среда" not in body["message"]


def test_next_school_day_skips_consecutive_holidays(fake_db, workdir, client):
    asyncio.run(add_holiday(date(2026, 11, 4), "День народного единства"))
    asyncio.run(add_holiday(date(2026, 11, 5), "Второй выходной подряд"))

    response = client.get("/next-bell", params={"at": "2026-11-03T18:00"})

    assert "Следующий учебный день — пятница" in response.json()["message"]


def test_regular_school_day_stays_school_day(fake_db, workdir, client):
    response = client.get("/next-bell", params={"at": "2026-11-05T10:00"})

    body = response.json()
    assert body["is_school_day"] is True
    assert body["current_event"]["type"] == "lesson"


def test_weekend_message_is_unchanged(fake_db, workdir, client):
    # 8 ноября 2026 — воскресенье: занятий нет и без календаря выходных
    response = client.get("/next-bell", params={"at": "2026-11-08T12:00"})

    body = response.json()
    assert body["is_school_day"] is False
    assert body["message"].startswith("🌙 Сегодня выходной — звонков нет.")


# --------------------------------------------------------------------------
# 4. Расписание группы
# --------------------------------------------------------------------------


def upload_schedule_with_wednesday(client, make_docx):
    groups = {
        "9-1": {
            "title": "Расписание уроков для 9-1 группы с 02.11.2026 г.",
            "lessons": {"1": {"Среда": "Химия Петров П.П. 101"}},
        }
    }
    path = make_docx("v1.docx", groups)
    response = client.post(
        "/schedule/upload",
        files={"schedule_file": ("v1.docx", path.read_bytes())},
    )
    assert response.status_code == 200


def test_schedule_on_holiday_is_404(
    fake_db, workdir, make_docx, holiday, client
):
    upload_schedule_with_wednesday(client, make_docx)

    response = client.get("/schedule/9-1", params={"date": "2026-11-04"})

    assert response.status_code == 404
    assert response.json()["detail"] == "4 ноября — выходной, занятий нет"


def test_schedule_on_school_day_still_served(
    fake_db, workdir, make_docx, holiday, client
):
    """Выходной 4-го не ломает обычную среду неделей позже."""
    upload_schedule_with_wednesday(client, make_docx)

    response = client.get("/schedule/9-1", params={"date": "2026-11-11"})

    assert response.status_code == 200
    assert response.json()["schedule"]["days"]["Среда"]["1"]["subject"] == "Химия"
