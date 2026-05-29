import json
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.bell_service import _update_all_schedules, MAIN_BELL_FILE

router = APIRouter()

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



