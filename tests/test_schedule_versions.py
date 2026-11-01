"""Разбор даты начала версии и выбор версии на дату — без базы."""

from datetime import date, datetime

import pytest

from app.services.schedule_versions import (
    applicable_versions,
    coerce_date,
    latest_per_group,
    merge_week,
    parse_requested_date,
    parse_title_date,
    resolve_day,
    version_start,
)


def version(valid_from, days=None, zero=None, valid_to=None, group="9-1"):
    """Документ версии в том виде, в каком он лежит в Mongo."""
    return {
        "group_name": group,
        "valid_from": datetime.combine(valid_from, datetime.min.time()),
        "valid_to": valid_to,
        "schedule": {
            "days": days or {},
            "zero_lesson": zero or {},
        },
    }


# --------------------------------------------------------------------------
# 1. Дата из заголовка документа
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Расписание уроков для 9-1 группы с 02.11.2026 г.",
        "Расписание уроков для 9-1 группы с 2.11.2026",
        "Расписание уроков для 9-1 группы с 02.11.2026",
    ],
)
def test_parse_title_date_three_formats(title):
    assert parse_title_date(title) == date(2026, 11, 2)


def test_parse_title_date_absent():
    assert parse_title_date("Расписание уроков для 9-1 группы") is None
    assert parse_title_date("") is None


def test_parse_title_date_rejects_impossible_date():
    assert parse_title_date("Расписание с 32.13.2026 г.") is None


def test_coerce_date_accepts_what_mongo_returns():
    assert coerce_date(datetime(2026, 11, 2)) == date(2026, 11, 2)
    assert coerce_date(date(2026, 11, 2)) == date(2026, 11, 2)
    assert coerce_date("2026-11-02T00:00:00") == date(2026, 11, 2)
    assert coerce_date("02.11.2026") == date(2026, 11, 2)
    assert coerce_date(None) is None


def test_parse_requested_date():
    assert parse_requested_date("2026-11-02") == date(2026, 11, 2)
    assert parse_requested_date("02.11.2026") == date(2026, 11, 2)
    assert parse_requested_date(None) is None
    with pytest.raises(ValueError):
        parse_requested_date("вчера")


# --------------------------------------------------------------------------
# 2. Выбор версии на дату
# --------------------------------------------------------------------------


def test_version_without_valid_from_counts_as_oldest():
    """Документы, залитые до появления версий, продолжают работать."""
    assert version_start({"group_name": "9-1"}) == date.min


def test_one_version_applies_from_its_start():
    docs = [version(date(2026, 11, 2))]

    assert applicable_versions(docs, date(2026, 11, 1)) == []
    assert applicable_versions(docs, date(2026, 11, 2)) == docs
    assert applicable_versions(docs, date(2026, 12, 31)) == docs


def test_two_versions_newest_first():
    first = version(date(2026, 11, 2))
    second = version(date(2026, 11, 5))
    docs = [first, second]

    assert applicable_versions(docs, date(2026, 11, 4)) == [first]
    assert applicable_versions(docs, date(2026, 11, 5)) == [second, first]
    assert applicable_versions(docs, date(2026, 11, 30)) == [second, first]


def test_valid_to_ends_version():
    docs = [version(date(2026, 11, 2), valid_to=date(2026, 11, 4))]

    assert applicable_versions(docs, date(2026, 11, 4)) == docs
    assert applicable_versions(docs, date(2026, 11, 5)) == []


# --------------------------------------------------------------------------
# 3. День из предыдущей версии
# --------------------------------------------------------------------------


def test_day_falls_back_to_previous_version():
    old = version(
        date(2026, 11, 2),
        days={"Вторник": {"1": {"subject": "Физика"}}, "Среда": {"1": {"subject": "Химия"}}},
    )
    new = version(date(2026, 11, 5), days={"Четверг": {"1": {"subject": "История"}}})
    versions = applicable_versions([old, new], date(2026, 11, 5))

    source, day = resolve_day(versions, "Четверг")
    assert source is new
    assert day["days"]["Четверг"]["1"]["subject"] == "История"

    source, day = resolve_day(versions, "Вт")
    assert source is old
    assert day["days"]["Вторник"]["1"]["subject"] == "Физика"


def test_empty_day_in_new_version_does_not_block_fallback():
    """В docx есть все дни, но пустые — это не «уроков нет», а «нет в этой версии»."""
    old = version(date(2026, 11, 2), days={"Среда": {"1": {"subject": "Химия"}}})
    new = version(date(2026, 11, 5), days={"Среда": {}})
    versions = applicable_versions([old, new], date(2026, 11, 5))

    source, day = resolve_day(versions, "Среда")
    assert source is old
    assert day["days"]["Среда"]["1"]["subject"] == "Химия"


def test_day_absent_everywhere():
    old = version(date(2026, 11, 2), days={"Вторник": {"1": {"subject": "Физика"}}})
    new = version(date(2026, 11, 5), days={"Четверг": {"1": {"subject": "История"}}})
    versions = applicable_versions([old, new], date(2026, 11, 5))

    assert resolve_day(versions, "Суббота") is None


def test_merge_week_takes_each_day_from_newest_version():
    old = version(
        date(2026, 11, 2),
        days={"Вторник": {"1": {"subject": "Физика"}}, "Среда": {"1": {"subject": "Химия"}}},
    )
    new = version(date(2026, 11, 5), days={"Среда": {"1": {"subject": "История"}}})
    versions = applicable_versions([old, new], date(2026, 11, 5))

    merged = merge_week(versions)
    assert merged["days"]["Среда"]["1"]["subject"] == "История"
    assert merged["days"]["Вторник"]["1"]["subject"] == "Физика"


def test_latest_per_group():
    docs = [
        version(date(2026, 11, 2)),
        version(date(2026, 11, 5)),
        version(date(2026, 12, 1), group="9-2"),
    ]

    current = latest_per_group(docs, date(2026, 11, 6))
    by_group = {doc["group_name"]: doc for doc in current}

    assert version_start(by_group["9-1"]) == date(2026, 11, 5)
    # у 9-2 версия только на декабрь — группа не должна пропасть из списка
    assert version_start(by_group["9-2"]) == date(2026, 12, 1)
