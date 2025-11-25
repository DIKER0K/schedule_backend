import json, os
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.database import db
from app.utils.common import normalize_day_name

router = APIRouter(prefix="", tags=["Расписание звонков"])

MAIN_BELL_FILE = "bell_schedule.json"
OVERRIDE_FILE = "bell_schedule_overrides.json"


# === 1️⃣ Базовая загрузка (основное расписание) ===
@router.post("/upload", summary="Загрузить основное расписание звонков и обновить все пары")
async def upload_bell_schedule(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Нужен JSON-файл")

    try:
        content = await file.read()
        bell_data = _normalize_bell_data(json.loads(content))

        with open(MAIN_BELL_FILE, "w", encoding="utf-8") as f:
            json.dump(bell_data, f, ensure_ascii=False, indent=2)

        updated_count = await _update_all_schedules(bell_data)
        return {"message": f"✅ Обновлено расписаний: {updated_count} (основное)", "file_saved": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке: {e}")


# === 2️⃣ Загрузка частичного расписания для конкретного дня ===
@router.post("/upload/special", summary="Загрузить расписание звонков для конкретных дней")
async def upload_special_bell_schedule(file: UploadFile = File(...)):
    """
    Принимает JSON в формате:
    {
      "среда": {
        "1": {"1_shift": {"1": "09:00–10:00", "2": "10:10–11:10"}},
        "2": {"1_shift": {"1": "10:10–11:10", "2": "11:20–12:20"}},
        "default": {"1_shift": {"1": "08:30–09:30", "2": "09:40–10:40"}}
      }
    }
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Нужен JSON-файл")

    try:
        content = await file.read()
        override_data = _normalize_bell_data(json.loads(content))

        with open(OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(override_data, f, ensure_ascii=False, indent=2)

        # обновляем расписания только для указанных дней
        updated_count = await _update_all_schedules(override_data, only_days=list(override_data.keys()))

        return {
            "message": f"✅ Обновлено расписаний: {updated_count} (специальные дни: {', '.join(override_data.keys())})",
            "file_saved": True,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке: {e}")


# === 🔧 Общая функция обновления всех расписаний ===
def _normalize_bell_data(raw_data: dict) -> dict:
    """
    Унифицирует схему расписания звонков до формата
    bell_schedule[day][building_key][shift_key] -> {lesson_num: time}.

    * Если на уровне дня сразу лежат ключи `1_shift`, `2_shift` и т.п.,
      оборачиваем их в building_key = "default".
    * building_key приводится к str, чтобы числа/None сохранялись единообразно.
    """
    normalized: dict[str, dict] = {}

    for day_key, day_payload in (raw_data or {}).items():
        if not isinstance(day_payload, dict):
            continue

        # Старый формат: {"1_shift": {...}, "2_shift": {...}}
        if any(k.endswith("_shift") for k in day_payload.keys()):
            normalized[day_key] = {"default": day_payload}
            continue

        # Новый формат с корпусами
        building_map: dict[str, dict] = {}
        for building_key, building_payload in day_payload.items():
            if not isinstance(building_payload, dict):
                continue
            building_str = "default" if building_key in (None, "") else str(building_key)
            building_map[building_str] = building_payload

        normalized[day_key] = building_map

    return normalized


async def _update_all_schedules(bell_data: dict, only_days: list[str] | None = None):
    """
    Обновляет все расписания в БД, добавляя поле 'time' по расписанию звонков.
    Поддерживает оба варианта ключей: 'вторник-четверг' и 'вторник_четверг'.
    """
    normalized_bells = _normalize_bell_data(bell_data)
    allowed_days = [normalize_day_name(day) for day in only_days] if only_days else None
    schedules = await db.schedules.find().to_list(None)
    updated_count = 0

    for s in schedules:
        group_name = s.get("group_name")
        shift_info = s.get("shift_info", {})
        shift = shift_info.get("shift", 1)
        schedule = s.get("schedule", {})
        modified = False

        # проходим по zero_lesson и days
        for section in ["zero_lesson", "days"]:
            section_data = schedule.get(section, {})
            for day_name, lessons in section_data.items():
                normalized_day = normalize_day_name(day_name)

                # если обновляем только определённые дни
                if allowed_days and normalized_day not in allowed_days:
                    continue

                # определяем ключ для звонков
                key = normalized_day
                if normalized_day in ["вторник", "среда", "четверг"]:
                    key = "вторник-четверг"

                day_block = normalized_bells.get(key)
                if not day_block:
                    alt_key = key.replace("-", "_")
                    day_block = normalized_bells.get(alt_key)

                if not day_block:
                    continue

                building = shift_info.get("building")
                building_key = "default" if building in (None, "") else str(building)
                building_block = day_block.get(building_key) or day_block.get("default")

                if not building_block:
                    continue

                shift_key = f"{shift}_shift"
                bell_times = building_block.get(shift_key, {})

                if not bell_times:
                    default_block = day_block.get("default", {})
                    bell_times = default_block.get(shift_key, {})

                if not bell_times:
                    continue

                # применяем время для каждой пары
                for lesson_num, lesson_data in lessons.items():
                    # поддержка строковых ключей (например, "1", "2")
                    lesson_num_str = str(lesson_num).strip()
                    time_str = bell_times.get(lesson_num_str)
                    if time_str:
                        lesson_data["time"] = time_str
                        modified = True

        if modified:
            await db.schedules.update_one(
                {"group_name": group_name},
                {"$set": {"schedule": schedule}}
            )
            updated_count += 1

    return updated_count
