"""Распределение групп по кабинетам на период: парсер, загрузка, выдача.

Файл-образец — приказ «Распределение групп по кабинетам на 2 и 3 ноября 2026»:
таблица «Группа | Кабинет», блоки «1 СМЕНА» (21 группа) и «2 СМЕНА»
(23 группы), вместо номера у части групп стоит «ПП» или «ДО».
"""

import asyncio
import json
from datetime import date

import pytest
from docx import Document
from fastapi import HTTPException, UploadFile

from app.services.classroom_distribution import (
    distribution_for,
    normalize_group_name,
    parse_distribution_document,
    parse_period,
)
from app.services.schedule_service import ScheduleService

# Данные приказа — как в настоящем файле: 21 группа в первой смене,
# 23 во второй, 44 из 44 групп колледжа.
FIRST_SHIFT = [
    ("1Р-1", "221"),
    ("1Р-2", "215"),
    ("1ТГ-1", "206"),
    ("1ТГ-2", "217"),
    ("1ТД", "214"),
    ("1ПДО", "222"),
    ("2ПДО", "220"),
    ("2ТД", "212"),
    ("2ИС-1", "223"),
    ("2ИС-2", "201"),
    ("3ИС-1", "ПП"),
    ("3ИС-2", "218"),
    ("2ТГ-1", "116"),
    ("2ТГ-2", "219"),
    ("3ТГ-1", "ПП"),
    ("3ТГ-2", "207"),
    ("3ПДО", "117"),
    ("3ТД", "213"),
    ("4ИС-1", "ПП"),
    ("4ИС-2", "108"),
    ("4ПДО", "120"),
]

SECOND_SHIFT = [
    ("1А", "108"),
    ("1Б", "206"),
    ("1Д", "213"),
    ("1ПД-1", "121"),
    ("1ПД-2", "215"),
    ("1ПД-3", "223"),
    ("1Э", "ДО"),
    ("1П", "ДО"),
    ("2А", "222"),
    ("2Б", "217"),
    ("2Д", "116"),
    ("2ПД-1", "219"),
    ("2ПД-2", "220"),
    ("3ПД-1", "218"),
    ("3ПД-2", "214"),
    ("4ПД-1", "ПП"),
    ("4ПД-2", "ПП"),
    ("3А", "201"),
    ("3Б", "120"),
    ("3Д", "ПП"),
    ("4А", "221"),
    ("4Б", "212"),
    ("4Д", "ПП"),
]


def build_distribution(
    path,
    first_shift=None,
    second_shift=None,
    title="Распределение групп по кабинетам на 2 и 3 ноября 2026",
):
    """Собирает docx как у секретаря: заголовок абзацем + таблица на две колонки."""
    document = Document()
    document.add_paragraph("Распределение групп по кабинетам ")
    if title:
        document.add_paragraph(title)

    rows = [("Группа", "Кабинет"), ("1 СМЕНА", "1 СМЕНА")]
    rows += list(first_shift or [])
    rows += [("2 СМЕНА", "2 СМЕНА")]
    rows += list(second_shift or [])

    table = document.add_table(rows=len(rows), cols=2)
    for row_index, (left, right) in enumerate(rows):
        table.rows[row_index].cells[0].text = left
        table.rows[row_index].cells[1].text = right

    document.save(path)
    return path


@pytest.fixture
def make_distribution(tmp_path):
    def _make(name: str, **kwargs):
        return build_distribution(tmp_path / name, **kwargs)

    return _make


def upload_distribution(path, valid_from=None, valid_to=None, building=None):
    with open(path, "rb") as handle:
        file = UploadFile(filename=path.name, file=handle)
        return asyncio.run(
            ScheduleService.upload_classrooms(
                file, valid_from=valid_from, valid_to=valid_to, building=building
            )
        )


def upload_schedule(path, valid_from=None):
    with open(path, "rb") as handle:
        file = UploadFile(filename=path.name, file=handle)
        return asyncio.run(
            ScheduleService.upload_schedule(file, None, valid_from=valid_from)
        )


def get(group, day=None, on=None):
    return asyncio.run(ScheduleService.get_schedule_by_group(group, day, on))


def as_dict(response) -> dict:
    return response if isinstance(response, dict) else response.model_dump()


def week_lessons(day_names=("Понедельник", "Вторник", "Среда")):
    """Расписание на несколько дней: по одной паре в день."""
    return {
        "1": {
            day: f"{subject} {teacher} 102"
            for day, (subject, teacher) in zip(
                day_names,
                [("Математика", "Иванов И.И."), ("Физика", "Петров П.П."), ("Химия", "Сидорова С.С.")],
            )
        }
    }


def schedule_docx(groups, valid_from="02.11.2026"):
    """{группа: lessons} → словарь для make_docx с датой в заголовке."""
    return {
        group: {
            "title": f"Расписание уроков для {group} группы с {valid_from} г.",
            "lessons": lessons,
        }
        for group, lessons in groups.items()
    }


# --------------------------------------------------------------------------
# 1. Парсер файла распределения
# --------------------------------------------------------------------------


def test_parser_reads_both_shift_blocks(tmp_path):
    path = build_distribution(
        tmp_path / "distribution.docx",
        first_shift=FIRST_SHIFT,
        second_shift=SECOND_SHIFT,
    )

    parsed = parse_distribution_document(path)

    assert len(parsed["groups"]) == 44
    assert sum(1 for entry in parsed["groups"].values() if entry["shift"] == 1) == 21
    assert sum(1 for entry in parsed["groups"].values() if entry["shift"] == 2) == 23
    assert parsed["valid_from"] == date(2026, 11, 2)
    assert parsed["valid_to"] == date(2026, 11, 3)

    # «ПП» и «ДО» — пометка вместо номера, кабинета нет
    assert parsed["groups"]["3ИС-1"] == {
        "classroom": None,
        "classroom_note": "ПП",
        "shift": 1,
    }
    assert parsed["groups"]["1Э"] == {
        "classroom": None,
        "classroom_note": "ДО",
        "shift": 2,
    }
    without_classroom = [
        group for group, entry in parsed["groups"].items() if not entry["classroom"]
    ]
    notes = sorted(entry["classroom_note"] for entry in parsed["groups"].values() if entry["classroom_note"])
    # в настоящем файле пометок 9: «ПП» у 7 групп и «ДО» у 2
    assert len(without_classroom) == 9
    assert notes == ["ДО", "ДО"] + ["ПП"] * 7

    # обычная группа: номер кабинета и смена из блока
    assert parsed["groups"]["1Р-1"] == {
        "classroom": "221",
        "classroom_note": None,
        "shift": 1,
    }
    assert parsed["groups"]["4А"] == {
        "classroom": "221",
        "classroom_note": None,
        "shift": 2,
    }


def test_parser_handles_merged_block_rows_and_dashes(tmp_path):
    """«2 СМЕНА» в обеих колонках и группы с тире разбираются."""
    path = build_distribution(
        tmp_path / "distribution.docx",
        first_shift=[("9-1", "101")],
        second_shift=[("ПД-2", "202")],
    )

    parsed = parse_distribution_document(path)

    assert parsed["groups"]["9-1"]["shift"] == 1
    assert parsed["groups"]["ПД-2"] == {
        "classroom": "202",
        "classroom_note": None,
        "shift": 2,
    }
    assert normalize_group_name(" 9\u20131 ") == "9-1"


@pytest.mark.parametrize(
    "title, fallback_year, expected",
    [
        ("на 2 и 3 ноября 2026", None, (date(2026, 11, 2), date(2026, 11, 3))),
        ("со 2 по 3 ноября", 2026, (date(2026, 11, 2), date(2026, 11, 3))),
        ("2–3 ноября 2026", None, (date(2026, 11, 2), date(2026, 11, 3))),
        ("с 30 октября по 3 ноября 2026", None, (date(2026, 10, 30), date(2026, 11, 3))),
        ("на 2 ноября 2026", None, (date(2026, 11, 2), date(2026, 11, 2))),
        ("Распределение групп по кабинетам", 2026, None),
        ("на 32 ноября 2026", None, None),
    ],
)
def test_parse_period_from_title(title, fallback_year, expected):
    assert parse_period(title, fallback_year) == expected


# --------------------------------------------------------------------------
# 2. Загрузка: смена и кабинет попадают в версию расписания
# --------------------------------------------------------------------------


def test_upload_sets_shift_and_classroom_on_version(
    fake_db, workdir, make_docx, make_distribution
):
    upload_schedule(
        make_docx("v1.docx", schedule_docx({"9-1": week_lessons(), "9-2": week_lessons()}))
    )

    response = upload_distribution(
        make_distribution(
            "distribution.docx",
            first_shift=[("9-2", "105")],
            second_shift=[("9-1", "221")],
        ),
        building=2,
    )

    assert response.updated_groups == 2
    assert response.total_groups == 2
    assert response.first_shift == 1
    assert response.second_shift == 1
    assert response.groups_without_classroom == []
    assert response.building == 2
    assert response.valid_from == date(2026, 11, 2)
    assert response.valid_to == date(2026, 11, 3)

    # значения записаны в версию расписания, а не в группу
    stored = asyncio.run(fake_db.schedules.find_one({"group_name": "9-1"}))
    assert stored["shift"] == 2
    assert stored["classroom"] == "221"
    assert stored["building"] == 2
    assert "2" not in str(stored.get("shift_info") or {})

    on_period = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert on_period["shift"] == 2
    assert on_period["classroom"] == "221"
    assert on_period["building"] == 2
    assert on_period["classroom_note"] is None
    assert on_period["schedule"]["days"]["Вторник"]["1"]["subject"] == "Физика"

    first_shift_group = as_dict(get("9-2", "Вторник", date(2026, 11, 3)))
    assert first_shift_group["shift"] == 1
    assert first_shift_group["classroom"] == "105"


def test_upload_keeps_note_instead_of_classroom(
    fake_db, workdir, make_docx, make_distribution
):
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))

    response = upload_distribution(
        make_distribution("distribution.docx", first_shift=[("9-1", "ПП")])
    )

    assert response.groups_without_classroom == ["9-1"]

    served = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert served["classroom"] is None
    assert served["classroom_note"] == "ПП"


def test_group_absent_from_file_keeps_previous_values(
    fake_db, workdir, make_docx, make_distribution
):
    upload_schedule(
        make_docx("v1.docx", schedule_docx({"9-1": week_lessons(), "9-2": week_lessons()}))
    )

    upload_distribution(make_distribution("d1.docx", second_shift=[("9-1", "221")]))
    # во втором файле только 9-2: для 9-1 это не ошибка, прежние значения остаются
    response = upload_distribution(
        make_distribution("d2.docx", first_shift=[("9-2", "105")])
    )

    assert response.updated_groups == 1
    assert response.groups_without_schedule == []

    served = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert served["shift"] == 2
    assert served["classroom"] == "221"


def test_no_schedule_for_period_is_409(fake_db, workdir, make_distribution):
    with pytest.raises(HTTPException) as error:
        upload_distribution(
            make_distribution("distribution.docx", first_shift=[("9-1", "221")])
        )

    assert error.value.status_code == 409
    assert "сначала загрузите расписание на этот период" in error.value.detail.lower()


def test_schedule_starting_later_is_409(fake_db, workdir, make_docx, make_distribution):
    upload_schedule(
        make_docx("v2.docx", schedule_docx({"9-1": week_lessons()}, valid_from="05.11.2026"))
    )

    with pytest.raises(HTTPException) as error:
        upload_distribution(
            make_distribution("distribution.docx", first_shift=[("9-1", "221")])
        )

    assert error.value.status_code == 409


def test_period_without_dates_in_title_uses_request_params(
    fake_db, workdir, make_docx, make_distribution
):
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))

    response = upload_distribution(
        make_distribution(
            "distribution.docx",
            title="Распределение групп по кабинетам",
            first_shift=[("9-1", "221")],
        ),
        valid_from="2026-11-02",
        valid_to="2026-11-03",
    )

    assert response.valid_from == date(2026, 11, 2)
    assert response.valid_to == date(2026, 11, 3)


def test_missing_period_is_400(fake_db, workdir, make_docx, make_distribution):
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))

    with pytest.raises(HTTPException) as error:
        upload_distribution(
            make_distribution(
                "distribution.docx",
                title="Распределение групп по кабинетам",
                first_shift=[("9-1", "221")],
            )
        )

    assert error.value.status_code == 400
    assert "укажите valid_from" in error.value.detail


def test_second_period_on_same_version_is_409(
    fake_db, workdir, make_docx, make_distribution
):
    """Приказ на другую неделю не должен переписать данные прошлой."""
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))
    upload_distribution(make_distribution("d1.docx", second_shift=[("9-1", "221")]))

    with pytest.raises(HTTPException) as error:
        upload_distribution(
            make_distribution(
                "d2.docx",
                title="Распределение групп по кабинетам на 5 и 6 ноября 2026",
                first_shift=[("9-1", "105")],
            )
        )

    assert error.value.status_code == 409
    assert "уже загружено распределение на 02.11.2026 — 03.11.2026" in error.value.detail
    assert "Сначала загрузите расписание на 05.11.2026 — 06.11.2026" in error.value.detail

    # данные прежнего периода не тронуты
    served = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert served["shift"] == 2
    assert served["classroom"] == "221"


def test_reupload_same_period_replaces_values(
    fake_db, workdir, make_docx, make_distribution
):
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))
    upload_distribution(make_distribution("d1.docx", second_shift=[("9-1", "221")]))

    upload_distribution(make_distribution("d1_fixed.docx", second_shift=[("9-1", "105")]))

    served = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert served["classroom"] == "105"


def test_version_starting_inside_period_is_used(
    fake_db, workdir, make_docx, make_distribution
):
    """Расписание загрузили на 5 ноября, приказ — с 2 по 6 ноября."""
    upload_schedule(
        make_docx("v2.docx", schedule_docx({"9-1": week_lessons()}, valid_from="05.11.2026"))
    )

    response = upload_distribution(
        make_distribution(
            "d1.docx",
            title="Распределение групп по кабинетам на 2 и 6 ноября 2026",
            second_shift=[("9-1", "221")],
        )
    )

    assert response.updated_groups == 1
    assert response.valid_from == date(2026, 11, 2)
    assert response.valid_to == date(2026, 11, 6)

    served = as_dict(get("9-1", "Вторник", date(2026, 11, 5)))
    assert served["shift"] == 2
    assert served["classroom"] == "221"


# --------------------------------------------------------------------------
# 3. Выдача: период кончился — снова первая смена
# --------------------------------------------------------------------------


def test_after_period_group_returns_to_its_shift(
    fake_db, workdir, make_docx, make_distribution
):
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))
    upload_distribution(make_distribution("d1.docx", second_shift=[("9-1", "221")]))

    within = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert within["shift"] == 2
    assert within["classroom"] == "221"

    # 4 ноября приказ уже не действует: обычная смена и никакого кабинета
    after = as_dict(get("9-1", "Среда", date(2026, 11, 4)))
    assert after["shift"] == 1
    assert after["classroom"] is None
    assert after["building"] is None
    assert after["classroom_note"] is None


def test_distribution_for_day_outside_period_is_empty():
    version = {
        "shift": 2,
        "classroom": "221",
        "building": 2,
        "classrooms_valid_from": date(2026, 11, 2),
        "classrooms_valid_to": date(2026, 11, 3),
    }

    assert distribution_for(version, date(2026, 11, 3))["shift"] == 2
    assert distribution_for(version, date(2026, 11, 4))["shift"] is None
    assert distribution_for({}, date(2026, 11, 3))["classroom"] is None


def test_list_endpoint_reports_shift_and_classroom(
    fake_db, workdir, make_docx, make_distribution, monkeypatch
):
    import app.services.schedule_service as schedule_service

    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))
    upload_distribution(make_distribution("d1.docx", second_shift=[("9-1", "221")]))

    monkeypatch.setattr(schedule_service, "today", lambda: date(2026, 11, 3))
    schedules = asyncio.run(ScheduleService.get_all_schedules())

    assert len(schedules) == 1
    assert schedules[0].shift == 2
    assert schedules[0].classroom == "221"


# --------------------------------------------------------------------------
# 4. Время пар второй смены
# --------------------------------------------------------------------------


@pytest.fixture
def two_shift_bells(fake_db, workdir, monkeypatch):
    """Звонки обеих смен и настоящая подстановка времени при загрузке."""
    import app.services.schedule_service as schedule_service
    from app.services.bell_service import apply_bell_schedule_from_files

    monkeypatch.setattr(
        schedule_service, "apply_bell_schedule_from_files", apply_bell_schedule_from_files
    )
    bell_data = {
        "понедельник": {"1_shift": {"1": "09:00–10:35"}, "2_shift": {"1": "13:15–14:10"}},
        "вторник-четверг": {
            "1_shift": {"1": "09:00–10:35"},
            "2_shift": {"1": "14:20–15:15"},
        },
    }
    (workdir / "bell_schedule.json").write_text(
        json.dumps(bell_data, ensure_ascii=False), encoding="utf-8"
    )
    return workdir


def test_second_shift_takes_lesson_times_from_second_shift_grid(
    two_shift_bells, make_docx, make_distribution
):
    upload_schedule(make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})))

    # до приказа время пар — по первой смене (проставлено при загрузке)
    before = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert before["schedule"]["days"]["Вторник"]["1"]["time"] == "09:00–10:35"

    upload_distribution(make_distribution("d1.docx", second_shift=[("9-1", "221")]))

    after = as_dict(get("9-1", "Вторник", date(2026, 11, 3)))
    assert after["shift"] == 2
    assert after["schedule"]["days"]["Вторник"]["1"]["time"] == "14:20–15:15"

    # в день, когда приказ не действует, время снова по первой смене
    next_day = as_dict(get("9-1", "Среда", date(2026, 11, 4)))
    assert next_day["shift"] == 1
    assert next_day["schedule"]["days"]["Среда"]["1"]["time"] == "09:00–10:35"


# --------------------------------------------------------------------------
# 5. Проводка эндпоинта
# --------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def test_endpoint_uploads_distribution_and_serves_it(
    fake_db, workdir, make_docx, make_distribution, client
):
    client.post(
        "/schedule/upload",
        files={
            "schedule_file": (
                "v1.docx",
                make_docx("v1.docx", schedule_docx({"9-1": week_lessons()})).read_bytes(),
            )
        },
    )
    path = make_distribution("distribution.docx", second_shift=[("9-1", "221")])

    response = client.post(
        "/schedule/classrooms/upload",
        files={"file": ("distribution.docx", path.read_bytes())},
        data={"building": "2"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["updated_groups"] == 1
    assert body["total_groups"] == 1
    assert body["second_shift"] == 1
    assert body["building"] == 2
    assert body["valid_from"] == "2026-11-02"
    assert body["valid_to"] == "2026-11-03"

    served = client.get("/schedule/9-1", params={"date": "2026-11-03"}).json()
    assert served["shift"] == 2
    assert served["classroom"] == "221"
    assert served["building"] == 2
    # постоянная смена группы не переписана — переезд живёт в версии
    assert served["shift_info"] == {"shift": 1}


def test_endpoint_without_schedule_for_period_is_409(fake_db, workdir, make_distribution, client):
    path = make_distribution("distribution.docx", first_shift=[("9-1", "221")])

    response = client.post(
        "/schedule/classrooms/upload",
        files={"file": ("distribution.docx", path.read_bytes())},
    )

    assert response.status_code == 409
    assert "сначала загрузите расписание на этот период" in response.json()["detail"].lower()


def test_endpoint_requires_docx(fake_db, workdir, client):
    response = client.post(
        "/schedule/classrooms/upload",
        files={"file": ("distribution.pdf", b"not a docx")},
    )

    assert response.status_code == 400
    assert "нужен docx" in response.json()["detail"].lower()
