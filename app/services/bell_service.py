import json, os
from app.database import db
from app.utils.common import normalize_day_name

MAIN_BELL_FILE = "bell_schedule.json"


async def _update_all_schedules(bell_data: dict, only_days: list[str] | None = None):
    schedules = await db.schedules.find().to_list(None)
    updated_count = 0

    for s in schedules:
        group_name = s.get("group_name")
        shift_info = s.get("shift_info", {})
        shift = shift_info.get("shift", 1)
        schedule = s.get("schedule", {})
        modified = False

        for section in ["zero_lesson", "days"]:
            section_data = schedule.get(section, {})
            for day_name, lessons in section_data.items():
                normalized_day = normalize_day_name(day_name)

                if only_days and normalized_day not in only_days:
                    continue

                key = normalized_day
                if normalized_day in ["вторник", "среда", "четверг"]:
                    key = "вторник-четверг"

                if key not in bell_data:
                    alt_key = key.replace("-", "_")
                    if alt_key in bell_data:
                        key = alt_key

                shift_key = f"{shift}_shift"
                bell_times = bell_data.get(key, {}).get(shift_key, {})

                if not bell_times:
                    continue

                for lesson_num, lesson_data in lessons.items():
                    lesson_num_str = str(lesson_num).strip()
                    time_str = bell_times.get(lesson_num_str)
                    if time_str:
                        lesson_data["time"] = time_str
                        modified = True

        if modified:
            await db.schedules.update_one(
                {"group_name": group_name}, {"$set": {"schedule": schedule}}
            )
            updated_count += 1

    return updated_count


async def apply_bell_schedule_from_files():
    if not os.path.exists(MAIN_BELL_FILE):
        return 0

    with open(MAIN_BELL_FILE, "r", encoding="utf-8") as f:
        bell_data = json.load(f)

    return await _update_all_schedules(bell_data)
