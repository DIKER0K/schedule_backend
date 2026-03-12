import json, os
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.database import db
from app.utils.common import normalize_day_name

router = APIRouter()

MAIN_BELL_FILE = "bell_schedule.json"
OVERRIDE_FILE = "bell_schedule_overrides.json"


# === 1️⃣ Базовая загрузка (основное расписание) ===
@router.post(
    "/upload", summary="Загрузить основное расписание звонков и обновить все пары"
)
async def upload_bell_schedule(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Нужен JSON-файл")

    try:
        content = await file.read()
        bell_data = json.loads(content)

        with open(MAIN_BELL_FILE, "w", encoding="utf-8") as f:
            json.dump(bell_data, f, ensure_ascii=False, indent=2)

        updated_count = await _update_all_schedules(bell_data)
        return {
            "message": f"✅ Обновлено расписаний: {updated_count} (основное)",
            "file_saved": True,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке: {e}")


# === 2️⃣ Загрузка частичного расписания для конкретного дня ===
@router.post(
    "/upload/special", summary="Загрузить расписание звонков для конкретных дней"
)
async def upload_special_bell_schedule(file: UploadFile = File(...)):
    """
    Принимает JSON в формате:
    {
      "среда": {
        "1_shift": {"1": "09:00–10:00", "2": "10:10–11:10"},
        "2_shift": {"1": "11:30–12:30", "2": "12:40–13:40"}
      }
    }
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Нужен JSON-файл")

    try:
        content = await file.read()
        override_data = json.loads(content)

        with open(OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(override_data, f, ensure_ascii=False, indent=2)

        # обновляем расписания только для указанных дней
        updated_count = await _update_all_schedules(
            override_data, only_days=list(override_data.keys())
        )

        return {
            "message": f"✅ Обновлено расписаний: {updated_count} (специальные дни: {', '.join(override_data.keys())})",
            "file_saved": True,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке: {e}")


# === 🔧 Общая функция обновления всех расписаний ===
async def _update_all_schedules(bell_data: dict, only_days: list[str] | None = None):
    """
    Обновляет все расписания в БД, добавляя поле 'time' по расписанию звонков.
    Поддерживает оба варианта ключей: 'вторник-четверг' и 'вторник_четверг'.
    """
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
                if only_days and normalized_day not in only_days:
                    continue

                # определяем ключ для звонков
                key = normalized_day
                if normalized_day in ["вторник", "среда", "четверг"]:
                    key = "вторник-четверг"

                # fallback: если ключ с дефисом не найден — пробуем с подчёркиванием
                if key not in bell_data:
                    alt_key = key.replace("-", "_")
                    if alt_key in bell_data:
                        key = alt_key

                shift_key = f"{shift}_shift"
                bell_times = bell_data.get(key, {}).get(shift_key, {})

                # если всё равно не нашли — пропускаем
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
                {"group_name": group_name}, {"$set": {"schedule": schedule}}
            )
            updated_count += 1

    return updated_count
