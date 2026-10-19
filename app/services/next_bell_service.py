"""Логика «когда следующий звонок».

Считается по расписанию звонков школы (bell_schedule.json), а не по расписанию
конкретной группы: звонок общий для всей школы, поэтому ответ одинаковый для
всех групп и ролей.

Второй смены сейчас нет. Параметр shift оставлен на будущее: в выдачу попадает
только выбранная смена (по умолчанию первая).
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from app.models.next_bell import (
    CurrentEvent,
    EventType,
    NextBellInfo,
    NextBellResponse,
)

# Часовой пояс школы. Контейнер живёт в UTC, поэтому считаем явно.
TZ_NAME = os.getenv("SCHOOL_TZ", "Asia/Yekaterinburg")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:  # нет tzdata — считаем в UTC, но не падаем
    TZ = ZoneInfo("UTC")

_HERE = Path(__file__).resolve().parent

# Файл звонков. Первым берётся тот, который пишет админка (лежит в /app),
# последним — копия из репозитория, чтобы эндпоинт не умер после пересборки образа.
CANDIDATE_BELL_FILES = [
    os.getenv("BELL_SCHEDULE_FILE") or "",
    "bell_schedule.json",
    str(_HERE.parent / "data" / "bell_schedule.default.json"),
]
OVERRIDE_FILE = "bell_schedule_overrides.json"

WEEKDAY_TO_RU = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}

# Ключ дня в файле звонков (вт/ср/чт там склеены в «вторник-четверг»)
WEEKDAY_TO_BELL_KEY = {
    0: "понедельник",
    1: "вторник-четверг",
    2: "вторник-четверг",
    3: "вторник-четверг",
    4: "пятница",
    5: "суббота",
    6: None,
}

# В пятницу и понедельник слот 09:00–10:00 — это «Разговоры о важном» и линейка,
# а не пара (в файле звонков он идёт под номером 1).
SPECIAL_LESSON_LABELS = {
    ("пятница", "1"): "классный час «Разговоры о важном»",
    ("понедельник", "1"): "классный час «Разговоры о важном»",
}

_DASHES = ("\u2013", "\u2014", "\u2012", "\u2212", "\u2010", "\u2011")


# --------------------------------------------------------------------------
# Разбор времени
# --------------------------------------------------------------------------


def _hhmm_to_minutes(value: str) -> int:
    hours, minutes = value.strip().split(":")
    return int(hours) * 60 + int(minutes)


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_span(value) -> tuple:
    """'10:15–11:55' / '9:00-9:45' → (615, 715) в минутах."""
    text = str(value).strip()
    for dash in _DASHES:
        text = text.replace(dash, "-")
    parts = [p.strip() for p in text.split("-") if p.strip()]
    if len(parts) != 2:
        raise ValueError(f"не разобрать интервал: {value!r}")
    start, end = _hhmm_to_minutes(parts[0]), _hhmm_to_minutes(parts[1])
    if end <= start:
        raise ValueError(f"конец раньше начала: {value!r}")
    return start, end


# --------------------------------------------------------------------------
# Загрузка расписания звонков
# --------------------------------------------------------------------------


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _load_bell_data() -> dict:
    for path in CANDIDATE_BELL_FILES:
        if path and os.path.isfile(path):
            data = _read_json(path)
            if data:
                return data
    return {}


def _grid_for(data: dict, day_key: str, shift: int) -> dict:
    """Сетка звонков выбранной смены на день (+ ручные правки из админки)."""
    grid = dict((data.get(day_key) or {}).get(f"{shift}_shift") or {})

    overrides = _read_json(OVERRIDE_FILE) or {}
    override_grid = (overrides.get(day_key) or {}).get(f"{shift}_shift") or {}
    grid.update(override_grid)

    return grid


# --------------------------------------------------------------------------
# Построение таймлайна дня
# --------------------------------------------------------------------------


_ORDINAL = {1: ("первый", "первого"), 2: ("второй", "второго")}


def _lesson_forms(day_key: str, number: str, part: Optional[int]) -> dict:
    """Падежные формы названия занятия — чтобы фразы бота были грамотными."""
    if number == "0":
        nominative, genitive, accusative = "Нулевая пара", "нулевой пары", "нулевую пару"
        bell_to = "звонок на нулевую пару"
        bell_from = "звонок с нулевой пары"
        is_special = False
    elif (day_key, number) in SPECIAL_LESSON_LABELS:
        title = SPECIAL_LESSON_LABELS[(day_key, number)]
        nominative = title
        genitive = f"занятия {title}"
        accusative = title
        bell_to = f"звонок на {title}"
        bell_from = f"звонок с конца занятия {title}"
        is_special = True
    else:
        nominative, genitive, accusative = f"{number} пара", f"{number} пары", f"{number} пару"
        bell_to = f"звонок на {number} пару"
        bell_from = f"звонок с конца {number} пары"
        is_special = False

    if part and not is_special:
        ordinal_nom, ordinal_gen = _ORDINAL.get(part, (f"{part}-й", f"{part}-го"))
        bell_to = f"звонок на {ordinal_nom} час {number} пары"
        bell_from = f"звонок с конца {ordinal_gen} часа {number} пары"
        nominative = f"{nominative} ({part}-й час)"

    # Внутри фразы: обычные названия пишем со строчной, имя мероприятия — как есть
    inline = nominative if is_special else nominative.lower()

    return {
        "label": nominative,
        "label_genitive": genitive,
        "label_accusative": accusative,
        "label_inline": inline,
        "bell_to": bell_to,
        "bell_from": bell_from,
    }


def _build_intervals(grid: dict, day_key: str) -> list:
    """Пары и часы пар по возрастанию времени.

    В файле звонков есть и пара целиком («2»: 10:15–11:55), и её часы
    («2.1», «2.2»). Если есть оба часа — берём их, иначе берём пару целиком:
    иначе интервалы наложились бы друг на друга.
    """
    by_base = {}
    for key, value in grid.items():
        base = str(key).split(".")[0].strip()
        try:
            span = _parse_span(value)
        except (ValueError, AttributeError):
            continue
        by_base.setdefault(base, {})[str(key).strip()] = span

    intervals = []
    for base, items in by_base.items():
        halves = [k for k in (f"{base}.1", f"{base}.2") if k in items]
        if len(halves) == 2:
            for index, key in enumerate(halves, start=1):
                start, end = items[key]
                forms = _lesson_forms(day_key, base, index)
                intervals.append(
                    {
                        "type": "lesson",
                        "number": base,
                        "part": index,
                        **forms,
                        "start": start,
                        "end": end,
                        "start_str": _fmt(start),
                        "end_str": _fmt(end),
                    }
                )
        else:
            span = items.get(base) or list(items.values())[0]
            start, end = span
            forms = _lesson_forms(day_key, base, None)
            intervals.append(
                {
                    "type": "lesson",
                    "number": base,
                    "part": None,
                    **forms,
                    "start": start,
                    "end": end,
                    "start_str": _fmt(start),
                    "end_str": _fmt(end),
                }
            )

    intervals.sort(key=lambda item: (item["start"], item["number"]))
    return intervals


def _build_timeline(intervals: list) -> list:
    """Пары и перемены вперемешку, по возрастанию времени."""
    timeline = []
    for index, lesson in enumerate(intervals):
        timeline.append(lesson)
        if index + 1 < len(intervals):
            following = intervals[index + 1]
            if lesson["end"] < following["start"]:
                label = "перемена"
                timeline.append(
                    {
                        "type": "break",
                        "number": None,
                        "part": None,
                        # внутри пары перерыв между половинками — тоже перемена
                        "label": label,
                        "label_inline": label,
                        "start": lesson["end"],
                        "end": following["start"],
                        "start_str": lesson["end_str"],
                        "end_str": following["start_str"],
                    }
                )
    return timeline


def _next_school_day_first_bell(data: dict, shift: int, now: datetime):
    """Первый звонок ближайшего учебного дня (для вечера и выходных)."""
    for offset in range(1, 8):
        day = now + timedelta(days=offset)
        day_key = WEEKDAY_TO_BELL_KEY.get(day.weekday())
        if not day_key:
            continue
        intervals = _build_intervals(_grid_for(data, day_key, shift), day_key)
        if intervals:
            return WEEKDAY_TO_RU[day.weekday()], intervals[0]["start_str"]
    return None, None


# --------------------------------------------------------------------------
# Человекочитаемый текст
# --------------------------------------------------------------------------


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    number = abs(int(number))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if number % 10 in (2, 3, 4) and number % 100 not in (12, 13, 14):
        return few
    return many


def _human_minutes(minutes: int) -> str:
    return f"{minutes} {_plural_ru(minutes, 'минуту', 'минуты', 'минут')}"


def _human_duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    if hours:
        return f"{hours} ч"
    return _human_minutes(rest)


def _left_text(seconds: int) -> Optional[str]:
    """«через 12 минут» / «меньше минуты»."""
    if seconds is None:
        return None
    if seconds <= 0:
        return "вот-вот"
    minutes = -(-seconds // 60)  # округляем вверх
    return f"через {_human_minutes(minutes)}"


def _resolve_now(at: Optional[str]) -> datetime:
    if not at:
        return datetime.now(TZ)

    text = at.strip()
    if "T" not in text and " " not in text:
        parts = text.split(":")
        if len(parts) in (2, 3) and all(p.strip().isdigit() for p in parts):
            hour, minute = int(parts[0]), int(parts[1])
            second = int(parts[2]) if len(parts) == 3 else 0
            if hour > 23 or minute > 59 or second > 59:
                raise ValueError("at: неверное время")
            return datetime.now(TZ).replace(
                hour=hour, minute=minute, second=second, microsecond=0
            )
        raise ValueError("at: ожидается 'HH:MM' или ISO-время, например 2026-10-16T10:03")

    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("at: ожидается 'HH:MM' или ISO-время, например 2026-10-16T10:03")

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ)


def _lessons_payload(intervals: list) -> list:
    """Сетка пар дня для кнопок «когда N пара».

    Пары с половинками (2.1/2.2) сворачиваются в одну кнопку: берётся
    самый ранний старт и самый поздний конец. «0» идёт в конец списка.
    Для пар из двух часов отдаём оба часа и длину пересменка.
    """
    by_base = {}
    for item in intervals:
        if item.get("type") != "lesson":
            continue
        base = str(item["number"])
        slot = by_base.setdefault(
            base,
            {
                "number": base,
                "start": item["start"],
                "end": item["end"],
                "label": item["label"],
                "label_inline": item["label_inline"],
                "halves": [],
            },
        )
        slot["start"] = min(slot["start"], item["start"])
        slot["end"] = max(slot["end"], item["end"])
        if item.get("part"):
            slot["halves"].append(
                {"start": item["start"], "end": item["end"], "part": item["part"]}
            )

    result = []
    for base in sorted(
        by_base, key=lambda b: (by_base[b]["start"], _pair_sort_key(b))
    ):
        slot = by_base[base]
        halves = sorted(slot.get("halves") or [], key=lambda h: h["part"])
        # пересменок между часами
        break_minutes = None
        if len(halves) == 2:
            break_minutes = halves[1]["start"] - halves[0]["end"]
        result.append(
            {
                "number": base,
                "start": _fmt(slot["start"]),
                "end": _fmt(slot["end"]),
                "is_special": (base == "0"),
                "label": slot["label"],
                "label_inline": slot["label_inline"],
                "halves": [
                    {
                        "start": _fmt(h["start"]),
                        "end": _fmt(h["end"]),
                        "part": h["part"],
                    }
                    for h in halves
                ],
                "break_between_minutes": break_minutes,
            }
        )
    return result


def _pair_sort_key(number: str):
    """«0» в конец, «4» и «4.1» в числовом порядке."""
    try:
        return (1, int(number), "")
    except (TypeError, ValueError):
        return (0, 0, str(number))


# --------------------------------------------------------------------------
# Основная логика
# --------------------------------------------------------------------------


def _no_bells_response(
    now: datetime,
    day_key: Optional[str],
    shift: int,
    data: dict,
    headline: str,
) -> NextBellResponse:
    day_name, first_bell = _next_school_day_first_bell(data, shift, now)
    message = headline
    if first_bell:
        message += f"\n\n🗓 Следующий учебный день — {day_name.lower()}, первый звонок в {first_bell}."

    return NextBellResponse(
        current_time=now.strftime("%H:%M"),
        current_day=WEEKDAY_TO_RU[now.weekday()],
        current_shift=shift,
        is_school_day=day_key is not None,
        current_event=CurrentEvent(
            type=EventType.no_classes,
            label="занятий нет",
        ),
        next_bell=None,
        message=message,
    )


async def get_next_bell(shift: int = 1, at: Optional[str] = None) -> NextBellResponse:
    now = _resolve_now(at)
    weekday = now.weekday()
    day_key = WEEKDAY_TO_BELL_KEY.get(weekday)
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second

    data = _load_bell_data()

    if day_key is None:
        return _no_bells_response(
            now, day_key, shift, data, "🌙 Сегодня выходной — звонков нет."
        )

    intervals = _build_intervals(_grid_for(data, day_key, shift), day_key)
    if not intervals:
        return _no_bells_response(
            now, None, shift, data, "🤷 Расписание звонков на сегодня не найдено."
        )

    timeline = _build_timeline(intervals)
    first = timeline[0]
    last = timeline[-1]

    # --- Занятия ещё не начались -------------------------------------------
    if now_seconds < first["start"] * 60:
        seconds = first["start"] * 60 - now_seconds
        left = _left_text(seconds)
        message = f"🔔 Первый звонок в {first['start_str']} — {left}."
        message += f"\n\n📍 Занятия ещё не начались. Первым будет {first['bell_to']}."

        return NextBellResponse(
            current_time=now.strftime("%H:%M"),
            current_day=WEEKDAY_TO_RU[weekday],
            current_shift=shift,
            is_school_day=True,
            current_event=CurrentEvent(
                type=EventType.no_classes,
                label="занятия ещё не начались",
                remaining_minutes=-(-seconds // 60),
            ),
            next_bell=NextBellInfo(
                time=first["start_str"],
                type=EventType.lesson,
                lesson_number=first["number"],
                label=first["bell_to"],
                in_minutes=-(-seconds // 60),
                in_seconds=seconds,
            ),
            lessons=_lessons_payload(intervals),
            message=message,
        )

    # --- Занятия закончились ------------------------------------------------
    if now_seconds >= last["end"] * 60:
        day_name, first_bell = _next_school_day_first_bell(data, shift, now)
        message = (
            f"🌙 Звонков больше нет — занятия закончились в {last['end_str']}."
        )
        if first_bell:
            message += (
                f"\n\n🗓 Следующий учебный день — {day_name.lower()}, "
                f"первый звонок в {first_bell}."
            )

        return NextBellResponse(
            current_time=now.strftime("%H:%M"),
            current_day=WEEKDAY_TO_RU[weekday],
            current_shift=shift,
            is_school_day=True,
            current_event=CurrentEvent(
                type=EventType.no_classes,
                label="занятия закончились",
            ),
            next_bell=None,
            lessons=_lessons_payload(intervals),
            message=message,
        )

    # --- Идёт пара или перемена ---------------------------------------------
    for index, event in enumerate(timeline):
        if not (event["start"] * 60 <= now_seconds < event["end"] * 60):
            continue

        following = timeline[index + 1] if index + 1 < len(timeline) else None
        is_last = following is None
        seconds = event["end"] * 60 - now_seconds
        left = _left_text(seconds)

        if event["type"] == "lesson":
            current_event = CurrentEvent(
                type=EventType.lesson,
                start=event["start_str"],
                end=event["end_str"],
                label=event["label"],
                lesson_number=event["number"],
                remaining_minutes=-(-seconds // 60),
            )
            bell_label = event.get("bell_from", "звонок") if not is_last else "звонок с последней пары"
            lines = [f"🔔 Следующий звонок в {event['end_str']} — {left}."]
            lines.append(f"\n📍 Сейчас идёт {event['label_inline']}.")
            if is_last:
                lines.append(f"⏰ Занятия закончатся в {event['end_str']}.")
            elif following:
                if following["type"] == "break":
                    lines.append(
                        f"⏭ Потом {following['label']} до {following['end_str']} "
                        f"({_human_duration(following['end'] - following['start'])})."
                    )
                else:
                    lines.append(
                        f"⏭ Потом {following['label_inline']} "
                        f"({following['start_str']}–{following['end_str']})."
                    )
        else:
            current_event = CurrentEvent(
                type=EventType.break_,
                start=event["start_str"],
                end=event["end_str"],
                label=event["label"],
                remaining_minutes=-(-seconds // 60),
            )
            bell_label = (
                following.get("bell_to", "звонок")
                if following
                else "звонок"
            )
            lines = [f"🔔 Следующий звонок в {event['end_str']} — {left}."]
            lines.append(f"\n📍 Сейчас {event['label_inline']}.")
            if following:
                lines.append(f"⏭ Дальше — {following['label_inline']}.")

        bell = NextBellInfo(
            time=event["end_str"],
            type=EventType.lesson if event["type"] == "break" else EventType.break_,
            lesson_number=following["number"] if event["type"] == "break" and following else None,
            label=bell_label,
            in_minutes=-(-seconds // 60),
            in_seconds=seconds,
        )

        return NextBellResponse(
            current_time=now.strftime("%H:%M"),
            current_day=WEEKDAY_TO_RU[weekday],
            current_shift=shift,
            is_school_day=True,
            current_event=current_event,
            next_bell=bell,
            lessons=_lessons_payload(intervals),
            message="\n".join(lines),
        )

    # Сюда попасть не должны, но лучше честный ответ, чем 500
    return _no_bells_response(
        now, day_key, shift, data, "🤷 Не удалось определить следующий звонок."
    )