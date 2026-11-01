"""Календарь нерабочих дней колледжа.

Приказ администрации «4 ноября — выходной» — это не перенос звонков, а
полное отсутствие занятий. Такие даты живут отдельной коллекцией `holidays`:
один документ на дату (`{"date": "2026-11-04", "title": "..."}`), уникальность
по `date` держит индекс, который создаётся при старте приложения.

Список праздников учебного года заводится миграцией из
`app/data/holidays.default.json`. Миграция помечает себя документом в
коллекции `migrations` и поэтому проходит один раз: выходной, удалённый через
API (занятия перенесли), не вернётся после перезапуска контейнера.

Дата хранится строкой `ГГГГ-ММ-ДД`: так она сортируется и фильтруется по году
без часовых поясов — колледж живёт по Asia/Yekaterinburg, а контейнер по UTC.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.database import db

logger = logging.getLogger(__name__)

# Имя индекса — чтобы при старте было видно, что он уже есть.
INDEX_NAME = "date_unique"

# id миграции со списком нерабочих дней учебного года.
MIGRATION_ID = "holidays-2026-2027"

# Файл со списком дат: лежит в репозитории, потому что внутри контейнера
# ручные файлы живут только до пересборки образа.
DEFAULT_FILE = Path(__file__).resolve().parents[1] / "data" / "holidays.default.json"

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def fmt_day_month(day: date) -> str:
    """«2026-11-04» → «4 ноября» — так дата звучит в сообщениях бота."""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"


def _as_date(value) -> date:
    """date/datetime/строка → date. ValueError — если не разобрать."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("пустая дата")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    parts = text.replace("/", ".").split(".")
    if len(parts) == 3:
        try:
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        except ValueError:
            pass
    raise ValueError(f"{text!r}: ожидается 2026-11-04 или 04.11.2026")


async def ensure_holiday_indexes():
    """Индекс уникальности по дате: один выходной на дату, иначе 409 в API."""
    await db.holidays.create_index("date", unique=True, name=INDEX_NAME)


def _default_holidays() -> list:
    """Даты из файла миграции. Битые записи пропускаем, а не роняем старт."""
    try:
        data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        logger.warning("Список нерабочих дней не прочитан (%s)", exc)
        return []

    result = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        try:
            day = _as_date(entry.get("date"))
        except ValueError:
            continue
        if title:
            result.append({"date": day.isoformat(), "title": title})
    return result


async def seed_holidays() -> int:
    """Миграция списка нерабочих дней учебного года. Возвращает, сколько добавлено.

    Повторный запуск ничего не делает (метка миграции в `migrations`), а
    существующие даты не перезаписываются: название, поправленное через API,
    остаётся как есть.
    """
    if await db.migrations.find_one({"_id": MIGRATION_ID}):
        return 0

    added = 0
    for entry in _default_holidays():
        result = await db.holidays.update_one(
            {"date": entry["date"]},
            {"$setOnInsert": {**entry, "source": f"миграция {MIGRATION_ID}"}},
            upsert=True,
        )
        if result.upserted_id is not None:
            added += 1

    await db.migrations.update_one(
        {"_id": MIGRATION_ID},
        {"$set": {"applied_at": datetime.now(), "holidays_added": added}},
        upsert=True,
    )
    logger.info(
        "Календарь нерабочих дней: миграция %s применена, добавлено %s",
        MIGRATION_ID,
        added,
    )
    return added


async def list_holidays(year: Optional[int] = None) -> list:
    """Нерабочие дни по возрастанию даты; `year` — только за этот год."""
    query = {}
    if year is not None:
        query = {"date": {"$gte": f"{year:04d}-01-01", "$lte": f"{year:04d}-12-31"}}

    docs = await db.holidays.find(query).sort("date", 1).to_list(None)
    result = []
    for doc in docs:
        try:
            day = _as_date(doc.get("date"))
        except ValueError:  # запись, заведённая руками, не должна ронять выдачу
            logger.warning("Нерабочий день с битой датой пропущен: %r", doc.get("date"))
            continue
        result.append({"date": day, "title": str(doc.get("title") or "")})
    return result


async def holiday_on(day) -> Optional[dict]:
    """Выходной на дату или None: {'date': '2026-11-04', 'title': '...'}."""
    day = _as_date(day)
    doc = await db.holidays.find_one({"date": day.isoformat()})
    if not doc:
        return None
    return {"date": day.isoformat(), "title": str(doc.get("title") or "")}


async def holidays_between(start, end) -> dict:
    """Выходные в промежутке: {'2026-11-04': 'День народного единства'}.

    Нужно выдаче звонков: «следующий учебный день» перепрыгивает выходные,
    а праздники в приказе могут идти подряд.
    """
    first, last = _as_date(start), _as_date(end)
    if last < first:
        first, last = last, first
    docs = await db.holidays.find(
        {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}
    ).to_list(None)

    result = {}
    for doc in docs:
        try:
            key = _as_date(doc.get("date")).isoformat()
        except ValueError:
            continue
        result[key] = str(doc.get("title") or "")
    return result


async def add_holiday(day, title: str) -> dict:
    """Отметить дату выходным. Повторная дата — 409."""
    day = _as_date(day)
    title = str(title or "").strip()
    if not title:
        raise HTTPException(
            status_code=422, detail="Укажите название выходного дня"
        )

    key = day.isoformat()
    if await db.holidays.find_one({"date": key}):
        raise HTTPException(
            status_code=409,
            detail=f"{fmt_day_month(day)} {day.year} уже отмечено выходным",
        )

    doc = {"date": key, "title": title}
    try:
        await db.holidays.insert_one(dict(doc))
    except DuplicateKeyError:  # параллельный запрос успел раньше
        raise HTTPException(
            status_code=409,
            detail=f"{fmt_day_month(day)} {day.year} уже отмечено выходным",
        )
    return doc


async def remove_holiday(day) -> dict:
    """Убрать дату из выходных (например, занятия перенесли). Нет даты — 404."""
    day = _as_date(day)
    result = await db.holidays.delete_one({"date": day.isoformat()})
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"{fmt_day_month(day)} {day.year} не отмечено выходным",
        )
    return {"date": day, "message": f"{fmt_day_month(day)} — снова учебный день"}
