"""Время пар по звонкам не должно схлопывать версии расписания.

Каждая версия — отдельный документ, и подстановка времени (`_update_all_schedules`)
обязана писать только в ту версию, которую обработала.
"""

import asyncio
import json
from datetime import date, datetime

from app.services.bell_service import _update_all_schedules
from app.services.schedule_service import ScheduleService

BELL_DATA = {
    "понедельник": {"1_shift": {"1": "09:00–10:00", "2": "10:15–11:55"}},
    "вторник-четверг": {"1_shift": {"1": "09:00–10:35", "2": "10:45–12:30"}},
}


def stored(collection, valid_from):
    return asyncio.run(
        collection.find_one({"group_name": "9-1", "valid_from": valid_from})
    )


def as_dict(response) -> dict:
    """Ответ сервиса — либо Schedule, либо словарь (выдача на один день)."""
    return response if isinstance(response, dict) else response.model_dump()


def upload(path, valid_from=None):
    from fastapi import UploadFile

    with open(path, "rb") as handle:
        file = UploadFile(filename=path.name, file=handle)
        return asyncio.run(
            ScheduleService.upload_schedule(file, None, valid_from=valid_from)
        )


def get(group, day, on):
    return asyncio.run(ScheduleService.get_schedule_by_group(group, day, on))


def test_bell_times_do_not_overwrite_other_version_days(fake_db):
    """Две версии одной группы с разными днями сохраняют свой состав."""
    collection = fake_db.schedules
    asyncio.run(
        collection.insert_one(
            {
                "group_name": "9-1",
                "valid_from": datetime(2026, 11, 2),
                "shift_info": {"shift": 1},
                "schedule": {"days": {"Вторник": {"1": {"subject": "Физика"}}}},
            }
        )
    )
    asyncio.run(
        collection.insert_one(
            {
                "group_name": "9-1",
                "valid_from": datetime(2026, 11, 5),
                "shift_info": {"shift": 1},
                "schedule": {"days": {"Четверг": {"1": {"subject": "История"}}}},
            }
        )
    )

    updated = asyncio.run(_update_all_schedules(BELL_DATA))

    assert updated == 2  # время проставлено в обе версии

    first = stored(collection, datetime(2026, 11, 2))
    second = stored(collection, datetime(2026, 11, 5))

    assert list(first["schedule"]["days"]) == ["Вторник"]
    assert first["schedule"]["days"]["Вторник"]["1"] == {
        "subject": "Физика",
        "time": "09:00–10:35",
    }

    assert list(second["schedule"]["days"]) == ["Четверг"]
    assert second["schedule"]["days"]["Четверг"]["1"] == {
        "subject": "История",
        "time": "09:00–10:35",
    }


def test_second_upload_keeps_first_version_when_bell_file_exists(
    bell_files, fake_db, make_docx
):
    """Загрузка второго приказа при наличии bell_schedule.json не затирает первый."""
    first = {
        "9-1": {
            "title": "Расписание уроков для 9-1 группы с 02.11.2026 г.",
            "lessons": {"1": {"Вторник": "Физика Петров П.П. 102"}},
        }
    }
    second = {
        "9-1": {
            "title": "Расписание уроков для 9-1 группы с 05.11.2026 г.",
            "lessons": {"1": {"Четверг": "История Кузнецов К.К. 104"}},
        }
    }

    upload(make_docx("v1.docx", first))
    upload(make_docx("v2.docx", second))

    tuesday = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    lesson = tuesday["schedule"]["days"]["Вторник"]["1"]
    assert lesson["subject"] == "Физика"
    assert lesson["time"] == "09:00–10:35"
    assert tuesday["valid_from"] == date(2026, 11, 2)

    thursday = as_dict(get("9-1", "Четверг", date(2026, 11, 5)))
    assert thursday["schedule"]["days"]["Четверг"]["1"]["subject"] == "История"
    assert thursday["valid_from"] == date(2026, 11, 5)

    # в базе обе версии со своим составом: время пар есть в каждой,
    # содержимое версий не переписано друг другом
    first_doc = stored(fake_db.schedules, datetime(2026, 11, 2))
    second_doc = stored(fake_db.schedules, datetime(2026, 11, 5))

    assert first_doc["schedule"]["days"]["Вторник"]["1"]["subject"] == "Физика"
    assert first_doc["schedule"]["days"]["Вторник"]["1"]["time"] == "09:00–10:35"
    assert "История" not in json.dumps(first_doc["schedule"], ensure_ascii=False)

    assert second_doc["schedule"]["days"]["Четверг"]["1"]["subject"] == "История"
    assert second_doc["schedule"]["days"]["Четверг"]["1"]["time"] == "09:00–10:35"
    assert "Физика" not in json.dumps(second_doc["schedule"], ensure_ascii=False)
