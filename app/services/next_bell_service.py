from datetime import datetime
from app.database import db
from app.models.next_bell import (
    CurrentEvent,
    EventType,
    NextBellInfo,
    NextBellResponse,
)

WEEKDAY_TO_RU = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}


def _parse_time(t: str) -> tuple[int, int]:
    t = t.strip().replace("–", "-").replace("—", "-").replace("\u2013", "-")
    parts = t.split("-")
    h, m = parts[0].strip().split(":")
    return int(h), int(m)


def _time_to_minutes(t: str) -> int:
    h, m = _parse_time(t)
    return h * 60 + m


def _minutes_to_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _build_timeline(lessons: dict) -> list[dict]:
    parsed = []
    for num, lesson_data in lessons.items():
        time_str = lesson_data.get("time") if isinstance(lesson_data, dict) else None
        if not time_str:
            continue
        clean = time_str.strip().replace("–", "-").replace("—", "-").replace("\u2013", "-")
        parts = clean.split("-")
        if len(parts) != 2:
            continue
        try:
            start_min = _time_to_minutes(parts[0])
            end_min = _time_to_minutes(parts[1])
        except (ValueError, IndexError):
            continue
        parsed.append({
            "number": str(num),
            "start": start_min,
            "end": end_min,
            "start_str": parts[0].strip(),
            "end_str": parts[1].strip(),
        })

    parsed.sort(key=lambda x: (x["start"], x["number"]))

    timeline = []
    for i, lesson in enumerate(parsed):
        timeline.append({
            "type": "lesson",
            "number": lesson["number"],
            "start": lesson["start"],
            "end": lesson["end"],
            "start_str": lesson["start_str"],
            "end_str": lesson["end_str"],
        })
        if i + 1 < len(parsed):
            next_lesson = parsed[i + 1]
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


async def _get_today_lessons() -> dict:
    today = WEEKDAY_TO_RU[datetime.now().weekday()]

    schedule_doc = await db.schedules.find_one(
        {"shift_info.shift": 1, f"schedule.days.{today}": {"$exists": True, "$ne": {}}}
    )
    if not schedule_doc:
        schedule_doc = await db.schedules.find_one(
            {"schedule.days.{today}": {"$exists": True, "$ne": {}}}
        )
    if not schedule_doc:
        return {}

    return schedule_doc.get("schedule", {}).get("days", {}).get(today, {})


async def get_next_bell() -> NextBellResponse:
    now = datetime.now()
    current_day = WEEKDAY_TO_RU[now.weekday()]
    current_time_str = now.strftime("%H:%M")
    current_minutes = now.hour * 60 + now.minute

    lessons = await _get_today_lessons()

    if not lessons:
        return NextBellResponse(
            current_time=current_time_str,
            current_day=current_day,
            current_event=CurrentEvent(type=EventType.no_classes),
        )

    timeline = _build_timeline(lessons)

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
            current_event=CurrentEvent(type=EventType.no_classes),
            next_bell=NextBellInfo(
                time=timeline[0]["start_str"],
                type=EventType.lesson,
                lesson_number=timeline[0]["number"],
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
