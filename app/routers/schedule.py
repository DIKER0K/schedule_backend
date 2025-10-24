from fastapi import APIRouter, UploadFile, File, HTTPException
from datetime import datetime
from bson import ObjectId
from app.database import db
from app.services.schedule_parser import parse_schedule_from_docx, load_group_shifts, add_classrooms_to_schedule
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

            # Добавляем кабинеты из group_shifts.json
            schedule_with_classrooms = add_classrooms_to_schedule(schedule, group, shifts)

            doc = {
                "group_name": group,
                "schedule": schedule_with_classrooms,  # ← Теперь с кабинетами!
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
    """Удаляет пробелы, точки и невидимые символы"""
    if not name:
        return ""
    name = name.strip().replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"[.\s]", "", name).lower()


def fio_matches(fio1: str, fio2: str) -> bool:
    """
    Гибкое сравнение ФИО (поддержка 'Фамилия Имя Отчество', 'Фамилия И.О.' и т.д.)
    """
    if not fio1 or not fio2:
        return False

    def normalize_fio(fio):
        fio = fio.strip()
        parts = re.split(r"[\s.]+", fio)
        parts = [p for p in parts if p]
        return parts

    p1, p2 = normalize_fio(fio1), normalize_fio(fio2)
    if not p1 or not p2:
        return False

    # фамилия должна совпадать
    if normalize_name(p1[0]) != normalize_name(p2[0]):
        return False

    # проверяем имя и отчество (инициалы)
    initials1 = "".join([w[0].lower() for w in p1[1:]])  # например, Дмитрий Александрович -> да
    initials2 = "".join([w[0].lower() for w in p2[1:]])

    return initials1.startswith(initials2) or initials2.startswith(initials1)


@router.get("/teacher/{fio:path}")
async def get_teacher_schedule(fio: str):
    fio = fio.strip()
    if not fio:
        raise HTTPException(status_code=400, detail="Некорректное ФИО преподавателя")

    schedules = await db.schedules.find().to_list(1000)
    teacher_schedule = {"first_shift": {}, "second_shift": {}}

    for s in schedules:
        group_name = s.get("group_name")
        schedule_data = s.get("schedule", {})
        if not schedule_data:
            continue

        shift = (s.get("shift_info") or {}).get("shift", 1)
        shift_key = "first_shift" if shift == 1 else "second_shift"

        def match_teacher(teacher: str) -> bool:
            return fio_matches(fio, teacher)

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