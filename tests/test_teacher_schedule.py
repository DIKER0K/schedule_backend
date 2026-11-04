"""Расписание преподавателя: дата, версия, кабинет группы и смена.

Проверяем то, что сломалось после перехода на версии расписания: эндпоинт брал
версию «на сегодня» независимо от запрошенного дня, из-за чего 1 ноября бот
показывал пары понедельника 2 ноября из СТАРОЙ версии (кабинет 205 и обычная
сетка звонков вместо приказа на 2 ноября), а кабинет искал только у пары,
хотя в приказе он закреплён за группой.
"""

import asyncio
import json
from datetime import date, datetime

import pytest
from docx import Document
from fastapi import HTTPException, UploadFile

from app.services.schedule_service import ScheduleService
from app.services.schedule_versions import mongo_date

TEACHER = "Ситников Д.А."


# --------------------------------------------------------------------------
# Данные
# --------------------------------------------------------------------------


def bell_files(workdir):
    """Звонки обычного дня: у первой и второй смены разное время.

    Разовых приказов на дату здесь нет — сетка берётся из дня недели, и
    разница «первая смена / вторая смена» видна без оглядки на приказ.
    """
    (workdir / "bell_schedule.json").write_text(
        json.dumps(
            {
                "понедельник": {
                    "1_shift": {"1": "09:00–10:00", "3": "11:00–11:50", "4": "12:00–12:50"},
                    "2_shift": {"1": "13:35–14:35", "3": "15:00–16:00", "4": "16:10–17:10"},
                },
                "вторник-четверг": {
                    "1_shift": {"1": "09:00–09:45"},
                    "2_shift": {"1": "12:55–13:40"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return workdir


@pytest.fixture
def bells(workdir):
    return bell_files(workdir)


def version(
    group,
    valid_from,
    days=None,
    zero=None,
    shift=1,
    distribution_shift=None,
    **extra,
):
    """Документ версии расписания в том виде, в каком он лежит в Mongo.

    `shift` — обычная смена группы (`shift_info`), `distribution_shift` — смена
    из приказа на период (поле версии, действует только вместе с границами
    `classrooms_valid_from/to`).
    """
    doc = {
        "group_name": group,
        "schedule": {"days": days or {}, "zero_lesson": zero or {}},
        "shift_info": {"shift": shift},
        "updated_at": datetime(2026, 10, 1, 12, 0),
        "valid_from": mongo_date(valid_from),
        "valid_to": None,
    }
    if distribution_shift is not None:
        doc["shift"] = distribution_shift
    doc.update(extra)
    return doc


def lesson(subject, classroom=None, teacher=TEACHER, time=None):
    data = {"subject": subject, "teacher": teacher, "classroom": classroom}
    if time:
        data["time"] = time
    return data


def insert(fake_db, *docs):
    for doc in docs:
        asyncio.run(fake_db.schedules.insert_one(doc))


def teacher_schedule(fio=TEACHER, day=None, on=None):
    return asyncio.run(ScheduleService.get_teacher_schedule(fio, day, on))


def as_dict(response) -> dict:
    return response if isinstance(response, dict) else response.model_dump()


def day_slots(response, shift="first_shift", day_name="Понедельник") -> dict:
    return as_dict(response)["schedule"][shift].get(day_name, {})


# --------------------------------------------------------------------------
# 1. Версия расписания выбирается по `date`, а не по «сегодня»
# --------------------------------------------------------------------------


def test_version_is_chosen_by_requested_date(fake_db, bells):
    insert(
        fake_db,
        version(
            "9-1",
            date(2026, 10, 1),
            days={"Понедельник": {"1": lesson("Старая информатика", "205")}},
        ),
        version(
            "9-1",
            date(2026, 11, 2),
            days={"Понедельник": {"1": lesson("Новая информатика")}},
        ),
    )

    old = day_slots(teacher_schedule(on=date(2026, 10, 20)))
    assert old["1"]["subject"] == "Старая информатика"
    assert old["1"]["classroom"] == "205"

    new = day_slots(teacher_schedule(on=date(2026, 11, 2)))
    assert new["1"]["subject"] == "Новая информатика"

    # дата не передана — берётся сегодняшняя (в контейнере это UTC, у школы
    # Asia/Yekaterinburg, поэтому «сегодня» приходит из today())
    assert as_dict(teacher_schedule())["requested_date"] == date.today()


def test_day_falls_back_to_previous_version(fake_db, bells):
    """Приказ на 2 ноября не принёс вторника — он берётся из прежней версии."""
    insert(
        fake_db,
        version(
            "9-1",
            date(2026, 10, 1),
            days={
                "Понедельник": {"1": lesson("Старая информатика", "205")},
                "Вторник": {"1": lesson("Старая физика", "205")},
            },
        ),
        version(
            "9-1",
            date(2026, 11, 2),
            days={"Понедельник": {"1": lesson("Новая информатика")}},
        ),
    )

    tuesday = day_slots(
        teacher_schedule(day="Вторник", on=date(2026, 11, 3)), day_name="Вторник"
    )
    assert tuesday["1"]["subject"] == "Старая физика"

    monday = day_slots(teacher_schedule(day="Понедельник", on=date(2026, 11, 2)))
    assert monday["1"]["subject"] == "Новая информатика"


# --------------------------------------------------------------------------
# 2. Кабинет: пара → группа из версии → пометка
# --------------------------------------------------------------------------


def test_pair_classroom_wins_over_group_classroom(fake_db, bells):
    insert(
        fake_db,
        version(
            "3ПДО",
            date(2026, 11, 2),
            days={"Понедельник": {"3": lesson("Педагогика", "310")}},
            classroom="117",
            building=2,
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
    )

    slots = day_slots(teacher_schedule(on=date(2026, 11, 2)))
    assert slots["3"]["classroom"] == "310"
    assert slots["3"]["classroom_note"] is None


def test_empty_pair_classroom_takes_group_classroom(fake_db, bells):
    """В приказе кабинет закреплён за группой — у пары его нет."""
    insert(
        fake_db,
        version(
            "3ПДО",
            date(2026, 10, 1),
            days={"Понедельник": {"3": lesson("Педагогика")}},
        ),
        version(
            "3ПДО",
            date(2026, 11, 2),
            days={"Понедельник": {"3": lesson("Педагогика")}},
            classroom="117",
            building=2,
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
    )

    slots = day_slots(teacher_schedule(on=date(2026, 11, 2)))
    assert slots["3"]["classroom"] == "117"

    # 20 октября приказ ещё не действует: кабинета у группы нет
    old = day_slots(teacher_schedule(on=date(2026, 10, 20)))
    assert old["3"]["classroom"] is None


@pytest.mark.parametrize("note", ["ДО"])
def test_classroom_note_instead_of_number(fake_db, bells, note):
    insert(
        fake_db,
        version(
            "3ИС-1",
            date(2026, 11, 2),
            days={"Понедельник": {"3": lesson("Практика")}},
            classroom=None,
            building=2,
            classroom_note=note,
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
    )

    slots = day_slots(teacher_schedule(on=date(2026, 11, 2)))
    assert slots["3"]["classroom"] is None
    assert slots["3"]["classroom_note"] == note


def test_practice_pairs_are_hidden_from_teacher(fake_db, bells):
    """Пара с пометкой «ПП» не попадает в расписание преподавателя.

    Практика в приказе помечена вместо номера кабинета («ПП»), и владелец
    попросил не показывать такие пары преподавателю: он и так знает, что
    группа на практике, а строка занимает место. Обычные пары того же дня
    при этом остаются, и нумерация не сдвигается.
    """
    insert(
        fake_db,
        version(
            "3ИС-1",
            date(2026, 11, 2),
            days={
                "Понедельник": {
                    "2": lesson("ПП. 03.01 Практика", classroom=None),
                    "3": lesson("Практика", classroom=None),
                    "4": lesson("МДК 07.01", classroom="201"),
                }
            },
            building=2,
            classroom_note="ПП",
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
        version(
            "2ИС-2",
            date(2026, 11, 2),
            days={"Понедельник": {"4": lesson("МДК 07.01", classroom="201")}},
            classroom="201",
            building=2,
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
    )

    slots = day_slots(teacher_schedule(on=date(2026, 11, 2)))
    assert "2" not in slots, "пара с пометкой ПП не должна показываться"
    assert "3" not in slots
    assert slots["4"]["subject"] == "МДК 07.01"
    assert slots["4"]["classroom"] == "201"


def test_day_with_only_practice_is_204(fake_db, bells):
    """Если в день у преподавателя только практика — «пар нет», а не пустой список."""
    insert(
        fake_db,
        version(
            "3ИС-1",
            date(2026, 11, 2),
            days={"Понедельник": {"1": lesson("Практика")}},
            building=2,
            classroom_note="ПП",
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
    )

    with pytest.raises(HTTPException) as exc:
        teacher_schedule(day="Понедельник", on=date(2026, 11, 2))
    assert exc.value.status_code == 204


def test_group_classroom_applies_to_parallel_groups(fake_db, bells):
    """Одна пара у двух групп: у каждой свой кабинет из приказа."""
    period = {
        "classrooms_valid_from": mongo_date(date(2026, 11, 2)),
        "classrooms_valid_to": mongo_date(date(2026, 11, 3)),
    }
    insert(
        fake_db,
        version(
            "3ПДО",
            date(2026, 11, 2),
            days={"Понедельник": {"3": lesson("Педагогика")}},
            classroom="117",
            **period,
        ),
        version(
            "4ИС-2",
            date(2026, 11, 2),
            days={"Понедельник": {"3": lesson("Педагогика")}},
            classroom="108",
            **period,
        ),
    )

    slots = day_slots(teacher_schedule(on=date(2026, 11, 2)))
    rooms = {slots["3"]["group"]: slots["3"]["classroom"]}
    rooms.update({p["group"]: p["classroom"] for p in slots["3"]["parallels"]})
    assert rooms == {"3ПДО": "117", "4ИС-2": "108"}


# --------------------------------------------------------------------------
# 3. Смена группы и время пар из её сетки звонков
# --------------------------------------------------------------------------


def test_second_shift_group_takes_time_from_second_shift_grid(fake_db, bells):
    insert(
        fake_db,
        version(
            "1А",
            date(2026, 10, 1),
            days={"Понедельник": {"1": lesson("Математика", "201")}},
            shift=2,
        ),
        version(
            "9-1",
            date(2026, 10, 1),
            days={"Понедельник": {"1": lesson("Математика", "201")}},
            shift=1,
        ),
    )

    served = as_dict(teacher_schedule(on=date(2026, 11, 9)))
    second = served["schedule"]["second_shift"]["Понедельник"]["1"]
    first = served["schedule"]["first_shift"]["Понедельник"]["1"]

    assert second["group"] == "1А"
    assert second["time"] == "13:35–14:35"
    assert first["group"] == "9-1"
    assert first["time"] == "09:00–10:00"


def test_order_moves_group_to_second_shift_only_for_period(fake_db, bells):
    """4ИС-2 на 2 ноября учится во вторую смену, дальше — в первую."""
    insert(
        fake_db,
        version(
            "4ИС-2",
            date(2026, 11, 2),
            days={
                "Понедельник": {"1": lesson("Информатика")},
                "Среда": {"1": lesson("Информатика")},
            },
            shift=1,
            distribution_shift=2,
            classroom="108",
            classrooms_valid_from=mongo_date(date(2026, 11, 2)),
            classrooms_valid_to=mongo_date(date(2026, 11, 3)),
        ),
    )

    during = as_dict(teacher_schedule(on=date(2026, 11, 2)))
    assert during["schedule"]["second_shift"]["Понедельник"]["1"]["time"] == "13:35–14:35"
    assert during["schedule"]["second_shift"]["Понедельник"]["1"]["classroom"] == "108"
    assert during["schedule"]["first_shift"] == {}

    # 4 ноября приказ кончился: снова первая смена и её сетка звонков
    after = as_dict(teacher_schedule(on=date(2026, 11, 4)))
    assert after["schedule"]["first_shift"]["Среда"]["1"]["time"] == "09:00–09:45"
    assert after["schedule"]["first_shift"]["Среда"]["1"]["classroom"] is None


# --------------------------------------------------------------------------
# 4. Ошибки
# --------------------------------------------------------------------------


def test_unpublished_date_is_404(fake_db, bells):
    insert(
        fake_db,
        version(
            "9-1",
            date(2026, 11, 2),
            days={"Понедельник": {"1": lesson("Информатика", "205")}},
        ),
    )

    with pytest.raises(HTTPException) as error:
        teacher_schedule(on=date(2026, 11, 1))

    assert error.value.status_code == 404
    assert "ещё не опубликовано" in error.value.detail


def test_unknown_teacher_is_404(fake_db, bells):
    insert(
        fake_db,
        version(
            "9-1",
            date(2026, 11, 2),
            days={"Понедельник": {"1": lesson("Информатика", "205")}},
        ),
    )

    with pytest.raises(HTTPException) as error:
        teacher_schedule(fio="Никто Н.Н.", on=date(2026, 11, 2))

    assert error.value.status_code == 404
    assert "не найден" in error.value.detail


def test_day_without_lessons_is_204(fake_db, bells):
    insert(
        fake_db,
        version(
            "9-1",
            date(2026, 11, 2),
            days={"Понедельник": {"1": lesson("Информатика", "205")}},
        ),
    )

    with pytest.raises(HTTPException) as error:
        teacher_schedule(day="Суббота", on=date(2026, 11, 2))

    assert error.value.status_code == 204


# --------------------------------------------------------------------------
# 5. Проводка эндпоинта: расписание и приказ загружены файлами
# --------------------------------------------------------------------------

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]


def schedule_docx(path, groups, valid_from="02.11.2026"):
    """docx расписания: {группа: {номер пары: {день: «предмет преподаватель»}}}."""
    document = Document()
    for group, lessons in groups.items():
        document.add_paragraph(
            f"Расписание уроков для {group} группы с {valid_from} г."
        )
        numbers = sorted(lessons, key=lambda n: int(str(n).split(".")[0]))
        table = document.add_table(rows=1 + len(numbers), cols=1 + len(DAYS))
        for index, text in enumerate(["Пара"] + DAYS):
            table.rows[0].cells[index].text = text
        for row_index, number in enumerate(numbers, start=1):
            table.rows[row_index].cells[0].text = str(number)
            for day_index, day in enumerate(DAYS, start=1):
                table.rows[row_index].cells[day_index].text = lessons[number].get(day, "")
    document.save(path)
    return path


def distribution_docx(path, groups, title="Распределение групп по кабинетам на 2 и 3 ноября 2026"):
    """docx приказа: блок «1 СМЕНА» и таблица «Группа | Кабинет»."""
    document = Document()
    document.add_paragraph(title)
    rows = [("Группа", "Кабинет"), ("1 СМЕНА", "1 СМЕНА")] + list(groups)
    table = document.add_table(rows=len(rows), cols=2)
    for row_index, (left, right) in enumerate(rows):
        table.rows[row_index].cells[0].text = left
        table.rows[row_index].cells[1].text = right
    document.save(path)
    return path


def upload_schedule(path):
    with open(path, "rb") as handle:
        file = UploadFile(filename=path.name, file=handle)
        return asyncio.run(ScheduleService.upload_schedule(file, None))


def upload_classrooms(path, building=2):
    with open(path, "rb") as handle:
        file = UploadFile(filename=path.name, file=handle)
        return asyncio.run(ScheduleService.upload_classrooms(file, building=building))


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_endpoint_serves_order_day_with_group_classrooms(
    fake_db, workdir, bells, client
):
    """2 ноября: 3ПДО каб. 117 на 3 паре, 4ИС-2 каб. 108 на 4 паре."""
    upload_schedule(
        schedule_docx(
            workdir / "schedule.docx",
            {
                "3ПДО": {"3": {"Понедельник": "Педагогика Ситников Д.А."}},
                "4ИС-2": {"4": {"Понедельник": "Информатика Ситников Д.А."}},
            },
        )
    )
    upload_classrooms(
        distribution_docx(workdir / "distribution.docx", [("3ПДО", "117"), ("4ИС-2", "108")])
    )

    response = client.get("/schedule/teacher/Ситников Д.А.", params={"date": "2026-11-02"})
    assert response.status_code == 200

    body = response.json()
    assert body["requested_date"] == "2026-11-02"
    monday = body["schedule"]["first_shift"]["Понедельник"]
    assert monday["3"]["group"] == "3ПДО"
    assert monday["3"]["classroom"] == "117"
    assert monday["3"]["time"] == "11:00–11:50"
    assert monday["4"]["group"] == "4ИС-2"
    assert monday["4"]["classroom"] == "108"
    assert monday["4"]["time"] == "12:00–12:50"


def test_endpoint_day_filter(fake_db, workdir, bells, client):
    upload_schedule(
        schedule_docx(
            workdir / "schedule.docx",
            {
                "3ПДО": {
                    "3": {
                        "Понедельник": "Педагогика Ситников Д.А.",
                        "Вторник": "Педагогика Ситников Д.А.",
                    }
                }
            },
        )
    )

    response = client.get(
        "/schedule/teacher/Ситников Д.А.",
        params={"day": "Понедельник", "date": "2026-11-02"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["filtered_by_day"] == "Понедельник"
    assert set(body["schedule"]["first_shift"]) == {"Понедельник"}
    assert body["schedule"]["first_shift"]["Понедельник"]["3"]["time"] == "11:00–11:50"


def test_endpoint_unpublished_date(fake_db, workdir, bells, client):
    upload_schedule(
        schedule_docx(
            workdir / "schedule.docx",
            {"3ПДО": {"3": {"Понедельник": "Педагогика Ситников Д.А."}}},
        )
    )

    response = client.get("/schedule/teacher/Ситников Д.А.", params={"date": "2026-10-01"})

    assert response.status_code == 404
    assert "ещё не опубликовано" in response.json()["detail"]
