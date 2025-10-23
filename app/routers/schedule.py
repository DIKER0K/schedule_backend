from fastapi import APIRouter, UploadFile, File, HTTPException
from datetime import datetime
from bson import ObjectId
from app.database import db
from app.services.schedule_parser import parse_schedule_from_docx, load_group_shifts
import re

router = APIRouter()


def serialize_doc(doc):
    """Преобразует ObjectId в str для JSON-совместимости"""
    if not doc:
        return doc
    doc["_id"] = str(doc["_id"])
    return doc


# 📘 Получение всех расписаний
@router.get("/", response_model=list)
async def get_all_schedules():
    schedules = await db.schedules.find().to_list(100)
    return [serialize_doc(s) for s in schedules]


# 📘 Получение расписания по названию группы
@router.get("/{group_name}", response_model=dict)
async def get_schedule(group_name: str):
    schedule = await db.schedules.find_one({"group_name": group_name})
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return serialize_doc(schedule)


# 📤 Загрузка нового DOCX расписания (с заменой старого)
@router.post("/upload", response_model=dict)
async def upload_schedule(
    schedule_file: UploadFile = File(..., description="DOCX файл с расписанием"),
    shifts_file: UploadFile | None = File(None, description="(Необязательно) JSON файл с информацией о сменах")
):
    """
    Загружает файл .docx, парсит его и сохраняет расписания в MongoDB.
    Если передан также файл group_shifts.json — он обновляется вместе с расписанием.
    Старые записи полностью очищаются.
    """
    import os, json
    if not schedule_file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Нужен DOCX файл расписания")

    temp_docx = f"temp_{datetime.now().timestamp()}.docx"
    temp_json = None

    try:
        # сохраняем docx временно
        with open(temp_docx, "wb") as f:
            content = await schedule_file.read()
            f.write(content)

        # если прислали shifts.json — сохраняем и обновляем локально
        if shifts_file:
            temp_json = f"temp_{datetime.now().timestamp()}.json"
            with open(temp_json, "wb") as f:
                content = await shifts_file.read()
                f.write(content)
            # обновим локальный файл group_shifts.json
            with open(temp_json, "r", encoding="utf-8") as f:
                new_shifts = json.load(f)
            with open("group_shifts.json", "w", encoding="utf-8") as f:
                json.dump(new_shifts, f, ensure_ascii=False, indent=2)
            print(f"✅ Обновлён файл group_shifts.json ({len(new_shifts)} групп)")

        # парсим документ
        data = parse_schedule_from_docx(temp_docx)
        if not data:
            raise HTTPException(status_code=400, detail="Не удалось распарсить расписание")

        # загружаем смены (уже обновлённые)
        shifts = load_group_shifts()

        # очищаем старое расписание
        await db.schedules.delete_many({})

        inserted = []
        first_shift_count = 0
        second_shift_count = 0

        for group, schedule in data.items():
            shift_info = shifts.get(group, {"shift": 1})
            shift_num = shift_info.get("shift", 1)
            if shift_num == 1:
                first_shift_count += 1
            elif shift_num == 2:
                second_shift_count += 1

            doc = {
                "group_name": group,
                "schedule": schedule,
                "shift_info": shift_info,
                "updated_at": datetime.now(),
            }
            result = await db.schedules.insert_one(doc)
            inserted.append(str(result.inserted_id))

        return {
            "message": (
                f"✅ Расписание загружено для {len(inserted)} групп "
                f"({first_shift_count} — 1 смена, {second_shift_count} — 2 смена)"
            ),
            "inserted_ids": inserted,
            "total_groups": len(inserted),
            "first_shift": first_shift_count,
            "second_shift": second_shift_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при обработке файлов: {e}")

    finally:
        for path in [temp_docx, temp_json]:
            if path and os.path.exists(path):
                os.remove(path)


# 🔄 Добавление или обновление расписания вручную
@router.post("/", response_model=dict)
async def create_or_replace_schedule(schedule: dict):
    """
    Создаёт или заменяет расписание конкретной группы.
    """
    group_name = schedule.get("group_name")
    if not group_name:
        raise HTTPException(status_code=400, detail="Field 'group_name' is required")

    await db.schedules.update_one(
        {"group_name": group_name}, {"$set": schedule}, upsert=True
    )
    return {"message": f"Schedule for group '{group_name}' created or updated"}


# ❌ Удаление расписания группы
@router.delete("/{group_name}")
async def delete_schedule(group_name: str):
    result = await db.schedules.delete_one({"group_name": group_name})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"message": f"Schedule for '{group_name}' deleted"}


def normalize_name(name: str) -> str:
    """Удаляет все лишние пробелы, точки и невидимые символы"""
    if not name:
        return ""
    name = name.strip().replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    # убрать все пробелы и точки, всё в нижний регистр
    return re.sub(r"[.\s]", "", name).lower()

@router.get("/teacher/{fio:path}")
async def get_teacher_schedule(fio: str):
    """
    Гибкий поиск расписания преподавателя.
    Поддерживает 'Фамилия И.О.' или 'Фамилия Имя Отчество'.
    Работает даже если в ФИО есть точки, пробелы и разные символы.
    """
    fio = fio.strip()
    if not fio:
        raise HTTPException(status_code=400, detail="Некорректное ФИО преподавателя")

    fio_normalized = normalize_name(fio)

    # ❗ исправлено название коллекции — должно быть schedules, а не schedule
    schedules = await db.schedules.find().to_list(1000)
    teacher_schedule = {"first_shift": {}, "second_shift": {}}

    for s in schedules:
        group_name = s.get("group_name")
        schedule_data = s.get("schedule", {})
        if not schedule_data:
            continue

        shift = (s.get("shift_info") or {}).get("shift", 1)
        shift_key = "first_shift" if shift == 1 else "second_shift"

        # внутренняя функция проверки
        def match_teacher(teacher: str) -> bool:
            if not teacher:
                return False
            t_norm = normalize_name(teacher)
            # допускаем частичное совпадение
            return fio_normalized in t_norm or t_norm in fio_normalized

        # нулевая пара
        for day, zero in (schedule_data.get("zero_lesson") or {}).items():
            if zero and match_teacher(zero.get("teacher", "")):
                teacher_schedule[shift_key].setdefault(day, {})
                teacher_schedule[shift_key][day]["0"] = {
                    "subject": zero.get("subject", ""),
                    "group": group_name,
                    "classroom": zero.get("classroom", "")
                }

        # обычные пары
        for day, lessons in (schedule_data.get("days") or {}).items():
            for num, info in (lessons or {}).items():
                if info and match_teacher(info.get("teacher", "")):
                    teacher_schedule[shift_key].setdefault(day, {})
                    teacher_schedule[shift_key][day][num] = {
                        "subject": info.get("subject", ""),
                        "group": group_name,
                        "classroom": info.get("classroom", "")
                    }

    if not any(teacher_schedule["first_shift"].values()) and not any(teacher_schedule["second_shift"].values()):
        raise HTTPException(status_code=404, detail=f"Расписание для преподавателя '{fio}' не найдено")

    return {"teacher_fio": fio, "schedule": teacher_schedule}