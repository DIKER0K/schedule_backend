"""Загрузка версий расписания и выдача по дате (БД — mongomock)."""

import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

from app.services.schedule_service import ScheduleService


def upload(path, valid_from=None, replace_all=False):
    from fastapi import UploadFile

    with open(path, "rb") as handle:
        file = UploadFile(filename=path.name, file=handle)
        return asyncio.run(
            ScheduleService.upload_schedule(
                file, None, valid_from=valid_from, replace_all=replace_all
            )
        )


def get(group, day=None, on=None):
    return asyncio.run(ScheduleService.get_schedule_by_group(group, day, on))


def as_dict(response) -> dict:
    """Ответ сервиса — либо Schedule, либо словарь (выдача на один день)."""
    return response if isinstance(response, dict) else response.model_dump()


def day_names(response) -> dict:
    """{день: {номер пары: предмет}} из ответа сервиса."""
    return {
        day: {number: lesson["subject"] for number, lesson in lessons.items()}
        for day, lessons in as_dict(response)["schedule"]["days"].items()
    }


def first_version(valid_from):
    """Версия, в которой есть вторник (физика) и среда (химия)."""
    return {
        "9-1": {
            "title": f"Расписание уроков для 9-1 группы с {valid_from} г.",
            "lessons": {
                "1": {
                    "Понедельник": "Математика Иванов И.И. 101",
                    "Вторник": "Физика Петров П.П. 102",
                },
                "2": {"Среда": "Химия Сидорова С.С. 103"},
            },
        }
    }


def second_version(valid_from):
    """Новый приказ только на четверг."""
    return {
        "9-1": {
            "title": f"Расписание уроков для 9-1 группы с {valid_from} г.",
            "lessons": {"1": {"Четверг": "История Кузнецов К.К. 104"}},
        }
    }


# --------------------------------------------------------------------------
# Дата начала версии
# --------------------------------------------------------------------------


def test_upload_takes_date_from_title(fake_db, workdir, make_docx):
    path = make_docx("v1.docx", first_version("02.11.2026"))

    response = upload(path)

    assert response.valid_from == date(2026, 11, 2)
    assert response.total_groups == 1
    assert response.total_versions == 1
    assert response.dates == {"9-1": "2026-11-02"}


def test_upload_uses_valid_from_when_title_has_no_date(fake_db, workdir, make_docx):
    groups = {
        "9-1": {
            "title": "Расписание уроков для 9-1 группы",
            "lessons": {"1": {"Вторник": "Физика Петров П.П. 102"}},
        }
    }
    path = make_docx("v1.docx", groups)

    response = upload(path, valid_from="2026-11-02")

    assert response.valid_from == date(2026, 11, 2)
    assert response.total_versions == 1


def test_upload_without_any_date_is_400(fake_db, workdir, make_docx):
    groups = {
        "9-1": {
            "title": "Расписание уроков для 9-1 группы",
            "lessons": {"1": {"Вторник": "Физика Петров П.П. 102"}},
        }
    }
    path = make_docx("v1.docx", groups)

    with pytest.raises(HTTPException) as error:
        upload(path)

    assert error.value.status_code == 400
    assert (
        "не удалось определить дату начала: укажите valid_from"
        in error.value.detail.lower()
    )


def test_upload_with_broken_valid_from_is_400(fake_db, workdir, make_docx):
    path = make_docx("v1.docx", first_version("02.11.2026"))

    with pytest.raises(HTTPException) as error:
        upload(path, valid_from="вчера")

    assert error.value.status_code == 400


# --------------------------------------------------------------------------
# Загрузка без затирания
# --------------------------------------------------------------------------


def test_second_version_keeps_previous_one(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))
    response = upload(make_docx("v2.docx", second_version("05.11.2026")))

    assert response.total_versions == 2

    on_first = get("9-1", "Вторник", date(2026, 11, 3))
    assert as_dict(on_first)["valid_from"] == date(2026, 11, 2)
    assert day_names(on_first)["Вторник"]["1"] == "Физика"

    on_second = get("9-1", "Четверг", date(2026, 11, 5))
    assert as_dict(on_second)["valid_from"] == date(2026, 11, 5)
    assert day_names(on_second)["Четверг"]["1"] == "История"


def test_missing_day_comes_from_previous_version(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))
    upload(make_docx("v2.docx", second_version("05.11.2026")))

    # 5 ноября нового приказа на вторник нет — берём из прежнего
    response = get("9-1", "Вторник", date(2026, 11, 5))

    assert day_names(response)["Вторник"]["1"] == "Физика"
    assert as_dict(response)["valid_from"] == date(2026, 11, 2)


def test_upload_same_date_replaces_version(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))

    changed = first_version("02.11.2026")
    changed["9-1"]["lessons"]["1"]["Вторник"] = "Астрономия Орлов О.О. 105"
    response = upload(make_docx("v1_fixed.docx", changed))

    assert response.total_versions == 1
    assert day_names(get("9-1", "Вторник", date(2026, 11, 3)))["Вторник"]["1"] == (
        "Астрономия"
    )


def test_replace_all_drops_previous_versions(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))
    response = upload(make_docx("v2.docx", second_version("05.11.2026")), replace_all=True)

    assert response.replace_all is True
    assert response.total_versions == 1

    with pytest.raises(HTTPException) as error:
        get("9-1", "Вторник", date(2026, 11, 5))
    assert error.value.status_code == 404


# --------------------------------------------------------------------------
# Дата, на которую расписания ещё нет
# --------------------------------------------------------------------------


def test_date_before_first_version_is_404(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))

    with pytest.raises(HTTPException) as error:
        get("9-1", None, date(2026, 11, 1))

    assert error.value.status_code == 404
    assert error.value.detail == "Расписание на 01.11.2026 ещё не опубликовано"


def test_day_absent_in_all_versions_is_404(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))

    with pytest.raises(HTTPException) as error:
        get("9-1", "Суббота", date(2026, 11, 3))

    assert error.value.status_code == 404
    assert "Расписание на 03.11.2026 ещё не опубликовано" in error.value.detail


def test_unknown_group_is_404(fake_db, workdir):
    with pytest.raises(HTTPException) as error:
        get("77-7", None, date(2026, 11, 3))

    assert error.value.status_code == 404
    assert "не найдена" in error.value.detail


def test_full_schedule_merges_versions(fake_db, workdir, make_docx):
    upload(make_docx("v1.docx", first_version("02.11.2026")))
    upload(make_docx("v2.docx", second_version("05.11.2026")))

    response = get("9-1", None, date(2026, 11, 5))

    days = day_names(response)
    assert days["Вторник"]["1"] == "Физика"  # из версии 02.11
    assert days["Четверг"]["1"] == "История"  # из версии 05.11
    assert as_dict(response)["valid_from"] == date(2026, 11, 5)


def test_documents_without_valid_from_still_served(fake_db, workdir):
    """Расписание, залитое до появления версий, продолжает отдаваться."""
    asyncio.run(
        fake_db.schedules.insert_one(
            {
                "group_name": "9-1",
                "shift_info": {"shift": 1},
                "schedule": {"days": {"Вторник": {"1": {"subject": "Физика"}}}},
            }
        )
    )

    response = get("9-1", "Вторник", date(2026, 11, 3))

    assert day_names(response)["Вторник"]["1"] == "Физика"
    assert as_dict(response)["valid_from"] is None
