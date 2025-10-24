import os
import re
import json
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from docx import Document
from app.database import db

SCHEDULE_FILE = "Расписание.docx"
SHIFTS_FILE = "group_shifts.json"

days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

# ============== ФУНКЦИИ ПАРСИНГА ==============

def normalize_teacher_name(name: str):
    """Нормализует имя преподавателя в формат 'Фамилия И.О.'"""
    if not name:
        return None
    name = re.sub(r'\s+', ' ', name.strip())
    m = re.search(r'([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.[А-ЯЁ]\.?)', name)
    if m:
        name = m.group(1)
    name = re.sub(r'\s*\d{2,4}[А-Яа-я]?$', '', name)
    name = re.sub(r'([А-ЯЁ])\.([А-ЯЁ])$', r'\1.\2.', name)
    name = re.sub(r'\.\.', '.', name)
    name = re.sub(r'[^А-Яа-яЁё.\s-]', '', name).strip()
    if not re.match(r'^[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.[А-ЯЁ]\.?$', name):
        return None
    fam, ini = name.split(maxsplit=1)
    fam = "-".join(s[:1].upper() + s[1:].lower() for s in fam.split("-"))
    return f"{fam} {ini}"

def parse_lesson_info_fixed(cell_text: str):
    text = re.sub(r'\s+', ' ', cell_text.strip())
    if not text or text == '##':
        return None
    if len(text) < 2 or re.match(r'^\d+$', text):
        return None

    teacher_match = re.search(r'([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.?)$', text)
    teacher = teacher_match.group(1) if teacher_match else ''
    subject = re.sub(r'\s*' + re.escape(teacher) + r'\s*$', '', text).strip() if teacher else text

    return {
        "subject": subject or text,
        "teacher": teacher,
        "classroom": ""  # Кабинет будет добавлен из group_shifts.json
    }

def add_classrooms_to_schedule(schedule: dict, group_name: str, shifts: dict):
    """Добавляет кабинеты из group_shifts.json к урокам в расписании"""
    group_shift = shifts.get(group_name, {})
    classroom = group_shift.get("room", "")
    
    if not classroom or classroom == "":
        return schedule
    
    # Добавляем кабинет к нулевому уроку
    for day in schedule["zero_lesson"]:
        if schedule["zero_lesson"][day]:
            schedule["zero_lesson"][day]["classroom"] = classroom
    
    # Добавляем кабинет к обычным урокам
    for day in schedule["days"]:
        for lesson_num in schedule["days"][day]:
            if schedule["days"][day][lesson_num]:
                schedule["days"][day][lesson_num]["classroom"] = classroom
    
    return schedule

def parse_schedule_table_fixed(table, group_name: str, schedules: dict):
    """Парсит таблицу с расписанием"""
    rows = table.rows
    if not rows:
        return

    header = [cell.text.strip() for cell in rows[0].cells]
    if len(header) < 2:
        return

    day_columns = {}
    for idx, cell in enumerate(header[1:], 1):
        for day in days_ru:
            if day in cell:
                day_columns[idx] = day
                break

    for r in rows[1:]:
        cells = [c.text.strip() for c in r.cells]
        if not cells or not cells[0]:
            continue
        lesson_num = cells[0].strip()
        if not re.match(r'^\d+$', lesson_num):
            continue

        for idx, text in enumerate(cells[1:], 1):
            if idx not in day_columns or not text.strip():
                continue
            day = day_columns[idx]
            lesson_info = parse_lesson_info_fixed(text)
            if lesson_info:
                if lesson_num == "0":
                    schedules[group_name]["zero_lesson"][day] = lesson_info
                else:
                    schedules[group_name]["days"][day][lesson_num] = lesson_info

def parse_schedule_from_docx(file_path: str):
    """Парсит расписание из DOCX и возвращает dict"""
    doc = Document(file_path)
    schedules = {}
    current_group = None

    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        group_match = re.search(r'Расписание уроков\s+для\s+(.+?)\s+группы', text)
        if group_match:
            current_group = group_match.group(1).strip()
            schedules[current_group] = {
                "days": {day: {} for day in days_ru},
                "zero_lesson": {day: {} for day in days_ru}
            }
            continue

    tables = [t for t in doc.tables if any(day in "\n".join(cell.text for row in t.rows for cell in row.cells) for day in days_ru)]
    group_index = 0
    group_names = list(schedules.keys())

    for table in tables:
        if group_index >= len(group_names):
            break
        group_name = group_names[group_index]
        parse_schedule_table_fixed(table, group_name, schedules)
        group_index += 1

    return schedules

# ============== РАБОТА СО СМЕНАМИ И БД ==============

def load_group_shifts():
    if not os.path.exists(SHIFTS_FILE):
        return {}
    try:
        with open(SHIFTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            normalized = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    normalized[k] = {"shift": v.get("shift", 1), "room": v.get("room", "")}
                else:
                    normalized[k] = {"shift": v, "room": ""}
            return normalized
    except Exception as e:
        print(f"Ошибка загрузки смен: {e}")
        return {}

# ============== АСИНХРОННЫЙ ПЛАНИРОВЩИК ==============

async def load_schedule_to_db():
    """Парсит и сохраняет расписание в MongoDB"""
    if not os.path.exists(SCHEDULE_FILE):
        print(f"❌ Файл {SCHEDULE_FILE} не найден")
        return
    data = parse_schedule_from_docx(SCHEDULE_FILE)
    if not data:
        print("❌ Не удалось распарсить расписание.")
        return

    # Загрузка смен
    shifts = load_group_shifts()

    # Очистим старые записи
    await db.schedules.delete_many({})
    for group, schedule in data.items():
        # Добавляем кабинеты из group_shifts.json
        schedule_with_classrooms = add_classrooms_to_schedule(schedule, group, shifts)
        
        await db.schedules.insert_one({
            "group_name": group,
            "schedule": schedule_with_classrooms,
            "shift_info": shifts.get(group, {"shift": 1}),
            "updated_at": datetime.now()
        })
    print(f"✅ Залито расписание для {len(data)} групп в MongoDB")

async def update_schedule_loop(interval_hours=1):
    """Фоновая задача, обновляющая расписание каждые X часов"""
    while True:
        try:
            await load_schedule_to_db()
        except Exception as e:
            print(f"❌ Ошибка обновления расписания: {e}")
        await asyncio.sleep(interval_hours * 3600)

def start_background_schedule_updater():
    """Запускает асинхронный фоновой процесс"""
    asyncio.create_task(update_schedule_loop())
