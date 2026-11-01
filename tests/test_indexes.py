"""Индексы коллекции расписаний создаются в коде при старте."""

import asyncio
from datetime import datetime

from app.main import ensure_schedule_indexes


def test_old_unique_group_index_is_replaced(fake_db):
    """Старый уникальный индекс по group_name снимается, остаётся составной."""
    collection = fake_db.schedules
    asyncio.run(collection.create_index("group_name", unique=True))
    asyncio.run(collection.insert_one({"group_name": "9-1", "schedule": {"days": {}}}))

    asyncio.run(ensure_schedule_indexes())

    info = asyncio.run(collection.index_information())
    leftovers = [
        name
        for name, spec in info.items()
        if spec.get("key") == [("group_name", 1)] and spec.get("unique")
    ]

    assert leftovers == []
    assert info["group_name_valid_from"]["key"] == [
        ("group_name", 1),
        ("valid_from", 1),
    ]


def test_indexes_are_idempotent(fake_db):
    asyncio.run(ensure_schedule_indexes())
    asyncio.run(ensure_schedule_indexes())

    info = asyncio.run(fake_db.schedules.index_information())
    assert "group_name_valid_from" in info


def test_two_versions_of_one_group_can_be_stored(fake_db):
    """Составной уникальный индекс не мешает второй версии той же группы."""
    asyncio.run(ensure_schedule_indexes())
    collection = fake_db.schedules

    asyncio.run(
        collection.insert_one({"group_name": "9-1", "valid_from": datetime(2026, 11, 2)})
    )
    asyncio.run(
        collection.insert_one({"group_name": "9-1", "valid_from": datetime(2026, 11, 5)})
    )

    assert asyncio.run(collection.count_documents({"group_name": "9-1"})) == 2
