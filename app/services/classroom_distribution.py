"""Распределение групп по кабинетам на период.

Приказ администрации приходит отдельным документом: «Распределение групп по
кабинетам на 2 и 3 ноября 2026» — таблица «Группа | Кабинет», разбитая блоками
«1 СМЕНА» / «2 СМЕНА». На эти дни часть групп учится во вторую смену и у каждой
группы свой кабинет, а дальше снова первая смена.

Поэтому смена и кабинет живут не у группы (`group_shifts.json` и
`shift_info` не трогаем), а у ВЕРСИИ расписания, вместе с границами периода:
`shift`, `classroom`, `building`, `classroom_note` + `classrooms_valid_from` /
`classrooms_valid_to`. Без границ переезд во вторую смену растянулся бы на всю
версию, то есть до следующего приказа.

Модуль состоит из чистых функций: разбор дат, разбор docx, выбор версий на
период и значений, действующих на дату. Запись в Mongo — в
`app/services/schedule_service`.
"""

import re
from datetime import date, timedelta
from typing import Optional

from docx import Document

from app.services.schedule_versions import (
    MIN_DATE,
    applicable_versions,
    coerce_date,
    version_end,
    version_start,
)

# Месяцы в родительном/именительном падеже: «ноября», «ноябрь», «мая», «май».
# Порядок важен: «март» должен проверяться раньше «мая»-подобных основ.
MONTH_STEMS = [
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("мая", 5),
    ("май", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
]
MONTH_RE = re.compile("|".join(stem for stem, _ in MONTH_STEMS))

# Дни: 1–31. Ограничение по бокам, чтобы «2026» не разобралось как «20» и «26».
DAY_RE = re.compile(r"(?<!\d)\d{1,2}(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

# Блок-разделитель таблицы: «1 СМЕНА» / «2 СМЕНА» (значение в обеих колонках).
SHIFT_BLOCK_RE = re.compile(r"^(\d+)\s*смен", re.IGNORECASE)
# Корпус в заголовке: «во 2 корпусе», «2-й корпус».
BUILDING_RE = re.compile(r"(?<!\d)(\d)\s*-?\s*корпус", re.IGNORECASE)
# Номер кабинета: 221, 108, 12а.
CLASSROOM_RE = re.compile(r"^\d{1,4}[а-яa-z]?$", re.IGNORECASE)

HEADER_WORDS = {"группа", "кабинет", "аудитория"}

DASHES = ("\u2013", "\u2014", "\u2012", "\u2212", "\u2010", "\u2011")


def _clean(value) -> str:
    """Убирает лишние пробелы и приводит разные тире к обычному дефису."""
    text = str(value or "").replace("\xa0", " ")
    for dash in DASHES:
        text = text.replace(dash, "-")
    return re.sub(r"\s+", " ", text).strip()


def normalize_group_name(value) -> str:
    """Название группы в том же виде, в каком оно приходит из расписания."""
    return _clean(value)


def _month_number(word: str) -> Optional[int]:
    for stem, number in MONTH_STEMS:
        if word.startswith(stem):
            return number
    return None


# --------------------------------------------------------------------------
# Период из заголовка
# --------------------------------------------------------------------------


def parse_period(text: str, fallback_year: Optional[int] = None):
    """Период действия приказа из заголовка: (первый день, последний день).

    Понимает «на 2 и 3 ноября 2026», «со 2 по 3 ноября», «2–3 ноября 2026»,
    «с 30 октября по 3 ноября 2026». Если года в тексте нет — берётся
    `fallback_year`; если и его нет — период не определён (None).
    """
    normalized = _clean(text).lower()
    if not normalized:
        return None

    months = []
    for match in MONTH_RE.finditer(normalized):
        month = _month_number(match.group(0))
        if month is not None:
            months.append((match.start(), match.end(), month))
    if not months:
        return None

    year_match = YEAR_RE.search(normalized)
    if year_match:
        year = int(year_match.group(1))
    else:
        year = fallback_year
    if year is None:
        return None

    first_start, first_end, first_month = months[0]
    first_days = [int(day) for day in DAY_RE.findall(normalized[:first_start])]
    if not first_days:
        return None

    # «2 и 3 ноября» — две даты одного месяца; «2 ноября» — одна.
    start_day = first_days[-2] if len(first_days) >= 2 else first_days[-1]
    end_day = first_days[-1]
    end_month = first_month

    if len(months) > 1:
        second_start, _, second_month = months[1]
        second_days = [
            int(day) for day in DAY_RE.findall(normalized[first_end:second_start])
        ]
        if second_days:
            end_month = second_month
            end_day = second_days[-1]

    try:
        start = date(year, first_month, start_day)
        end = date(year, end_month, end_day)
    except ValueError:
        return None

    if end < start:
        # Период через Новый год: «с 30 декабря по 3 января».
        try:
            end = date(year + 1, end_month, end_day)
        except ValueError:
            return None
        if end < start:
            return None
    return start, end


# --------------------------------------------------------------------------
# Таблица «Группа | Кабинет»
# --------------------------------------------------------------------------


def classify_classroom(value) -> dict:
    """Значение колонки «Кабинет»: номер → кабинет, «ПП»/«ДО» → пометка."""
    text = _clean(value)
    if CLASSROOM_RE.match(text):
        return {"classroom": text, "classroom_note": None}
    return {"classroom": None, "classroom_note": text.upper()}


def parse_distribution_document(path, fallback_year: Optional[int] = None) -> dict:
    """Разбор docx распределения групп по кабинетам.

    Возвращает {"valid_from", "valid_to", "building", "groups"}, где groups —
    {группа: {"shift", "classroom", "classroom_note"}}.

    Дата периода берётся из заголовка абзаца, блок «N СМЕНА» задаёт смену для
    всех групп под ним, а «ПП»/«ДО» вместо номера — это пометка, кабинета нет.
    Группа, которой нет в файле, в словарь не попадает: её прежние значения
    остаются как есть.
    """
    document = Document(path)
    title = " ".join(p.text.strip() for p in document.paragraphs if p.text.strip())
    period = parse_period(title, fallback_year)
    building_match = BUILDING_RE.search(title)

    groups = {}
    for table in document.tables:
        current_shift = None
        for row in table.rows:
            cells = [_clean(cell.text) for cell in row.cells]
            values = {cell for cell in cells if cell}
            if not values:
                continue

            if len(values) == 1:
                # Строка, где обе колонки слиты: заголовок или блок «1 СМЕНА».
                single = next(iter(values))
                shift_match = SHIFT_BLOCK_RE.match(single)
                if shift_match:
                    current_shift = int(shift_match.group(1))
                continue

            group = normalize_group_name(cells[0])
            value = cells[-1] if len(cells) > 1 else ""
            if not group or not value:
                continue
            if group.lower() in HEADER_WORDS or value.lower() in HEADER_WORDS:
                continue

            entry = classify_classroom(value)
            entry["shift"] = current_shift
            groups[group] = entry

    return {
        "valid_from": period[0] if period else None,
        "valid_to": period[1] if period else None,
        "building": int(building_match.group(1)) if building_match else None,
        "groups": groups,
    }


# --------------------------------------------------------------------------
# Версия расписания на период и значения, действующие на дату
# --------------------------------------------------------------------------


def _version_windows(versions: list):
    """Окна действия версий группы: (документ, первый день, последний день).

    Версия действует до начала следующей — так же её читают `applicable_versions`
    и `merge_week` (дней, которых нет в новой версии, они берут из прежней).
    Поэтому окно версии заканчивается накануне следующей, даже если `valid_to`
    в документе не проставлен. `valid_to`, если он есть и раньше, важнее.
    """
    ordered = sorted(versions, key=version_start)
    windows = []
    for index, doc in enumerate(ordered):
        start = version_start(doc)
        end = version_end(doc)
        if index + 1 < len(ordered):
            next_start = version_start(ordered[index + 1])
            # date.min (документы без valid_from) — границы раньше не бывает.
            if next_start > MIN_DATE:
                before_next = next_start - timedelta(days=1)
                end = before_next if end is None else min(end, before_next)
        windows.append((doc, start, end))
    return windows


def versions_for_period(versions: list, start: Optional[date], end: Optional[date]):
    """Версии расписания группы, чьё окно действия пересекается с периодом.

    Приказ на 2–3 ноября может лечь сразу в несколько версий: расписание
    приходит отдельными файлами на каждый день, и на первые дни периода
    действуют разные версии. Пишем во все, иначе второй день периода молча
    остался бы без смены и кабинета. Порядок — от новой к старой.
    """
    if not versions or start is None:
        return []

    last = end or start
    picked = []
    for doc, window_start, window_end in _version_windows(versions):
        if window_start > last:
            continue
        if window_end is not None and window_end < start:
            continue
        picked.append(doc)
    return sorted(picked, key=version_start, reverse=True)


EMPTY_DISTRIBUTION = {
    "shift": None,
    "classroom": None,
    "building": None,
    "classroom_note": None,
}


def distribution_period(doc: dict):
    """Границы периода, на который действуют смена и кабинеты версии."""
    return (
        coerce_date((doc or {}).get("classrooms_valid_from")),
        coerce_date((doc or {}).get("classrooms_valid_to")),
    )


def distribution_for(doc: dict, day: date) -> dict:
    """Смена, кабинет, корпус и пометка версии, действующие на дату.

    Вне периода приказа возвращаются пустые значения: переезд во вторую смену
    действует только на 2–3 ноября, а дальше группа снова учится в свою
    обычную смену.
    """
    if not doc:
        return dict(EMPTY_DISTRIBUTION)

    start, end = distribution_period(doc)
    if start is None:
        return dict(EMPTY_DISTRIBUTION)
    if day < start or (end is not None and day > end):
        return dict(EMPTY_DISTRIBUTION)

    raw_shift = doc.get("shift")
    try:
        shift = int(raw_shift) if raw_shift not in (None, "") else None
    except (TypeError, ValueError):
        shift = None

    return {
        "shift": shift,
        "classroom": doc.get("classroom") or None,
        "building": doc.get("building"),
        "classroom_note": doc.get("classroom_note") or None,
    }


def distribution_on_date(versions: list, day: date) -> dict:
    """Смена, кабинет, корпус и пометка, действующие на дату, по всем версиям.

    Приказ пишется в версии, которые действовали в момент загрузки. Если позже
    пришла новая версия расписания (например, на 3 ноября отдельным файлом),
    распределение остаётся в прежней версии, но продолжает действовать:
    поэтому дату проверяем по окну `classrooms_valid_from/to` у каждой версии
    группы, начиная с действующей на эту дату версии.
    """
    if not versions:
        return dict(EMPTY_DISTRIBUTION)

    ordered = list(applicable_versions(versions, day))
    ordered += sorted(
        (doc for doc in versions if version_start(doc) > day),
        key=version_start,
        reverse=True,
    )

    for doc in ordered:
        found = distribution_for(doc, day)
        if any(value is not None for value in found.values()):
            return found
    return dict(EMPTY_DISTRIBUTION)

