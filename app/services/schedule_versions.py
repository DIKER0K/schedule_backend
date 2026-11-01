"""Версии расписания: с какой даты действует документ и какую версию отдавать.

Приказ приходит периодами: сначала «расписание с 02.11.2026», потом
«5, 6 и 7 ноября отправлю позже». Поэтому загрузка docx больше не затирает
базу: каждый файл создаёт версию на свою дату начала (`valid_from`), а выдача
берёт самую позднюю версию, которая уже началась на запрошенную дату.
Если в ней нужного дня нет — берётся предыдущая версия: так 2–3 ноября
приходят из нового приказа, а остальные дни — из прежнего.

Модуль намеренно состоит из чистых функций над словарями документов Mongo:
его можно проверять без базы.
"""

import re
from datetime import date, datetime, time
from typing import Iterable, Optional

from app.services.next_bell_service import TZ
from app.utils.common import normalize_day_name

# Дата начала в заголовке документа: «с 02.11.2026 г.», «с 2.11.2026».
TITLE_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")

# Версия без `valid_from` (документы, залитые до появления версий) действует
# «с начала времён» — иначе после выкатки в проде не нашлось бы расписания.
MIN_DATE = date.min


def parse_title_date(text: str) -> Optional[date]:
    """Дата начала из заголовка docx. Нет даты или она битая — None."""
    if not text:
        return None
    for match in TITLE_DATE_RE.finditer(text):
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            continue
    return None


def coerce_date(value) -> Optional[date]:
    """Приводит к date то, что лежит в базе или пришло в запросе."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(TZ).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return parse_title_date(text)


def parse_requested_date(value: Optional[str]) -> Optional[date]:
    """Дата из параметра запроса. ValueError — если строку не разобрать."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = coerce_date(text)
    if parsed is None:
        raise ValueError(
            f"{text!r}: ожидается 2026-11-02 или 02.11.2026"
        )
    return parsed


def today() -> date:
    """Сегодняшняя дата по часовому поясу школы (контейнер живёт в UTC)."""
    return datetime.now(TZ).date()


def mongo_date(value: date) -> datetime:
    """Как дата версии хранится в Mongo: полночь, без часового пояса."""
    return datetime.combine(value, time.min)


def fmt_date(value: Optional[date]) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def version_start(doc: dict) -> date:
    """С какой даты действует версия. Без valid_from — «всегда»."""
    return coerce_date(doc.get("valid_from")) or MIN_DATE


def version_end(doc: dict) -> Optional[date]:
    """По какую дату включительно действует версия. None — до следующей."""
    return coerce_date(doc.get("valid_to"))


def is_valid_at(doc: dict, day: date) -> bool:
    if version_start(doc) > day:
        return False
    end = version_end(doc)
    return end is None or end >= day


def applicable_versions(docs: Iterable[dict], day: date) -> list:
    """Версии, действующие на дату: от новой к старой."""
    valid = [doc for doc in docs if is_valid_at(doc, day)]
    return sorted(valid, key=version_start, reverse=True)


def _day_slice(schedule: dict, normalized_day: str) -> Optional[dict]:
    """Пары и нулевая пара одного дня, если они в этом дне есть."""
    zero = {}
    for day_name, info in (schedule.get("zero_lesson") or {}).items():
        if info and normalize_day_name(day_name) == normalized_day:
            zero[day_name] = info
            break

    days = {}
    for day_name, lessons in (schedule.get("days") or {}).items():
        if lessons and normalize_day_name(day_name) == normalized_day:
            days[day_name] = lessons
            break

    if not zero and not days:
        return None

    result = {}
    if zero:
        result["zero_lesson"] = zero
    if days:
        result["days"] = days
    return result


def resolve_day(versions: list, day_name: str):
    """День недели из самой новой версии, где он есть.

    `versions` — уже отфильтрованные и отсортированные от новой к старой
    (см. applicable_versions). Возвращает (документ версии, срез дня) или None.
    """
    normalized = normalize_day_name(day_name)
    for doc in versions:
        picked = _day_slice(doc.get("schedule") or {}, normalized)
        if picked:
            return doc, picked
    return None


def merge_week(versions: list) -> dict:
    """Неделя, собранная по версиям: день берётся из самой новой, где он есть."""
    zero, days = {}, {}
    for doc in versions:
        schedule = doc.get("schedule") or {}
        for day_name, info in (schedule.get("zero_lesson") or {}).items():
            if info and day_name not in zero:
                zero[day_name] = info
        for day_name, lessons in (schedule.get("days") or {}).items():
            if lessons and day_name not in days:
                days[day_name] = lessons
    return {"zero_lesson": zero, "days": days}


def latest_per_group(docs: Iterable[dict], day: date) -> list:
    """По одному документу на группу — та, что действует на дату.

    Нужен там, где раньше на группу приходился ровно один документ
    (список расписаний, расписание преподавателя). Если на дату не действует
    ни одна версия, остаётся самая поздняя — чтобы группа не пропадала из ответа.
    """
    by_group = {}
    for doc in docs:
        by_group.setdefault(doc.get("group_name"), []).append(doc)

    result = []
    for versions in by_group.values():
        usable = applicable_versions(versions, day)
        if usable:
            result.append(usable[0])
        else:
            result.append(sorted(versions, key=version_start, reverse=True)[0])
    return result
