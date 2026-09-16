import json
import os
from datetime import datetime
from typing import Optional

from app.models.next_bell import (
    CurrentEvent,
    EventType,
    NextBellInfo,
    NextBellResponse,
)

MAIN_BELL_FILE = "bell_schedule.json"
OVERRIDE_FILE = "bell_schedule_overrides.json"

DAY_MAP = {
    "понедельник": "понедельник",
    "вторник": "вторник-четверг",
    "среда": "вторник-четверг",
    "четверг": "вторник-четверг",
    "пятница": "пятница",
    "суббота": "суббота",
}

DAY_NAMES_RU = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

WEEKDAY_TO_RU = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}


def _load_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_time(t: str) -> tuple[int, int]:
    t = t.strip().replace("–", "-").replace("—", "-")
    parts = t.split("-")
    h, m = parts[0].strip().split(":")
    return int(h), int(m)


def _time_to_minutes(t: str) -> int:
    h, m = _parse_time(t)
    return h * 60 + m


def _minutes_to_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _get_bell_key(day_ru: str) -> Optional[str]:
    return DAY_MAP.get(day_ru.lower())


def _get_bell_schedule_for_today(day_ru: str) -> dict:
    main = _load_json(MAIN_BELL_FILE)
    overrides = _load_json(OVERRIDE_FILE)

    bell_key = _get_bell_key(day_ru)
    if not bell_key:
        return {}

    schedule = {}
    if main and bell_key in main:
        schedule = main[bell_key]
    if overrides and day_ru.lower() in overrides:
        schedule = {**schedule, **overrides[day_ru.lower()]}

    return schedule


def _build_timeline(bell_times: dict) -> list[dict]:
    lessons = []
    for num, time_str in bell_times.items():
        if not time_str or "-" not in time_str and "–" not in time_str:
            continue
        clean = time_str.strip().replace("–", "-").replace("—", "-")
        parts = clean.split("-")
        if len(parts) != 2:
            continue
        start_min = _time_to_minutes(parts[0])
        end_min = _time_to_minutes(parts[1])
        lessons.append({
            "number": num,
            "start": start_min,
            "end": end_min,
            "start_str": parts[0].strip(),
            "end_str": parts[1].strip(),
        })

    lessons.sort(key=lambda x: (x["start"], x["number"]))

    timeline = []
    for i, lesson in enumerate(lessons):
        timeline.append({
            "type": "lesson",
            "number": lesson["number"],
            "start": lesson["start"],
            "end": lesson["end"],
            "start_str": lesson["start_str"],
            "end_str": lesson["end_str"],
        })
        if i + 1 < len(lessons):
            next_lesson = lessons[i + 1]
            if lesson["end"] < next_lesson["start"]:
                timeline.append({
                    "type": "break",
                    "number": None,
                    "start": lesson["end"],
                    "end": next_lesson["start"],
                    "start_str": lesson["end_str"],
                    "end_str": next_lesson["start_str"],
                })

    return timeline


def get_next_bell() -> NextBellResponse:
    now = datetime.now()
    current_day = WEEKDAY_TO_RU[now.weekday()]
    current_time_str = now.strftime("%H:%M")
    current_minutes = now.hour * 60 + now.minute

    bell_schedule = _get_bell_schedule_for_today(current_day)
    shift_key = "1_shift"
    bell_times = bell_schedule.get(shift_key, {})

    if not bell_times:
        return NextBellResponse(
            current_time=current_time_str,
            current_day=current_day,
            current_event=CurrentEvent(type=EventType.no_classes),
        )

    timeline = _build_timeline(bell_times)

    if not timeline:
        return NextBellResponse(
            current_time=current_time_str,
            current_day=current_day,
            current_event=CurrentEvent(type=EventType.no_classes),
        )

    first_start = timeline[0]["start"]
    last_end = timeline[-1]["end"]

    if current_minutes < first_start:
        return NextBellResponse(
            current_time=current_time_str,
            current_day=current_day,
            current_event=CurrentEvent(
                type=EventType.no_classes,
                start=_minutes_to_time(first_start),
                end=timeline[0]["start_str"],
            ),
            next_bell=NextBellInfo(
                time=timeline[0]["start_str"],
                type=EventType.lesson if timeline[0]["type"] == "lesson" else EventType.break_,
                lesson_number=timeline[0].get("number"),
            ),
        )

    if current_minutes >= last_end:
        return NextBellResponse(
            current_time=current_time_str,
            current_day=current_day,
            current_event=CurrentEvent(type=EventType.no_classes),
        )

    for i, event in enumerate(timeline):
        if event["start"] <= current_minutes < event["end"]:
            event_type = EventType.lesson if event["type"] == "lesson" else EventType.break_
            current_event = CurrentEvent(
                type=event_type,
                start=event["start_str"],
                end=event["end_str"],
            )

            next_event = None
            if i + 1 < len(timeline):
                ne = timeline[i + 1]
                ne_type = EventType.lesson if ne["type"] == "lesson" else EventType.break_
                next_event = NextBellInfo(
                    time=ne["start_str"],
                    type=ne_type,
                    lesson_number=ne.get("number"),
                )

            return NextBellResponse(
                current_time=current_time_str,
                current_day=current_day,
                current_event=current_event,
                next_bell=next_event,
            )

    return NextBellResponse(
        current_time=current_time_str,
        current_day=current_day,
        current_event=CurrentEvent(type=EventType.no_classes),
    )
