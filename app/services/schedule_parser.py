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
    name = re.sub(r"\s+", " ", name.strip())
    m = re.search(r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.[А-ЯЁ]\.?)", name)
    if m:
        name = m.group(1)
    name = re.sub(r"\s*\d{2,4}[А-Яа-я]?$", "", name)
    name = re.sub(r"([А-ЯЁ])\.([А-ЯЁ])$", r"\1.\2.", name)
    name = re.sub(r"\.\.", ".", name)
    name = re.sub(r"[^А-Яа-яЁё.\s-]", "", name).strip()
    if not re.match(r"^[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.[А-ЯЁ]\.?$", name):
        return None
    fam, ini = name.split(maxsplit=1)
    fam = "-".join(s[:1].upper() + s[1:].lower() for s in fam.split("-"))
    return f"{fam} {ini}"


def parse_lesson_info_fixed(cell_text: str):
    """
    Парсит предмет, преподавателя и кабинет из ячейки DOCX.
    Учитывает:
    - МДК 07.01 ...
    - кабинет рядом с преподавателем
    """

    if not cell_text:
        return None

    # сохраняем переносы строк
    lines = [l.strip() for l in cell_text.split("\n") if l.strip()]
    text = " ".join(lines)

    if not text or text == "##":
        return None

    teacher = None
    classroom = None

    # --- ищем преподавателя ---
    teacher_match = re.search(
        r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.[А-ЯЁ]\.?)", text
    )

    if teacher_match:
        teacher = teacher_match.group(1)

        # ищем кабинет рядом с преподавателем
        after_teacher = text[teacher_match.end() :]

        room_match = re.search(r"\b\d{2,3}\b", after_teacher)
        if room_match:
            classroom = room_match.group(0)

    # --- предмет ---
    subject = text

    if teacher:
        subject = subject.replace(teacher, "")

    if classroom:
        subject = subject.replace(classroom, "")

    subject = re.sub(r"\s+", " ", subject).strip()

    # если кабинет всё ещё внутри subject — убираем
    if subject and classroom:
        subject = re.sub(rf"\b{classroom}\b", "", subject).strip()

    if not teacher:
        print("⚠ Не найден преподаватель:", cell_text)

    return {
        "subject": subject if subject else None,
        "teacher": teacher,
        "classroom": classroom,
    }


def add_classrooms_to_schedule(schedule: dict, group_name: str, shifts: dict):
    """
    Добавляет кабинеты из group_shifts.json,
    НО только если они отсутствуют в расписании.
    """

    group_shift = shifts.get(group_name, {})
    classroom = group_shift.get("room")

    if not classroom:
        return schedule

    for day in schedule["zero_lesson"]:
        lesson = schedule["zero_lesson"][day]
        if lesson and not lesson.get("classroom"):
            lesson["classroom"] = classroom

    for day in schedule["days"]:
        for lesson_num, lesson in schedule["days"][day].items():
            if lesson and not lesson.get("classroom"):
                lesson["classroom"] = classroom

    return schedule


def parse_schedule_table_fixed(table, group_name: str, schedules: dict):
    """Обновленная логика парсинга с группировкой строк по номерам пар"""
    print(f"\n--- [ТАБЛИЦА] Начало парсинга для группы: {group_name} ---")
    rows = table.rows
    if not rows:
        return

    header = [cell.text.strip() for cell in rows[0].cells]
    if len(header) < 2:
        return

    # Определяем дни недели
    day_columns = {}
    for idx, cell in enumerate(header[1:], 1):
        for day in days_ru:
            if day in cell:
                day_columns[idx] = day
                break

    # 1. Группируем строки таблицы по номеру пары (чтобы отслеживать половинки)
    pairs_rows = defaultdict(list)
    current_lesson = None

    for r_idx, r in enumerate(rows[1:], 1):
        cells = [c.text.strip() for c in r.cells]
        if not cells:
            continue

        first_cell = cells[0].strip()
        # Если в первой колонке стоит цифра - начинается новая пара
        if re.match(r"^\d+$", first_cell):
            current_lesson = first_cell
        elif not current_lesson:
            continue  # Пропускаем строки без номера пары в начале

        pairs_rows[current_lesson].append(cells)

    # 2. Разбираем каждую пару отдельно
    for lesson_num, rows_data in pairs_rows.items():
        num_subrows = len(
            rows_data
        )  # Количество строк в паре (1 = целая, 2 = есть половинки)
        day_entries = defaultdict(list)

        # Собираем все непустые записи для каждого дня
        for subrow_idx, cells in enumerate(rows_data):
            for idx, text in enumerate(cells[1:], 1):
                if idx not in day_columns:
                    continue
                if not text.strip():
                    continue

                day = day_columns[idx]
                lesson_info = parse_lesson_info_fixed(text)
                if not lesson_info:
                    continue

                # Создаем уникальную сигнатуру, чтобы не добавлять дубликаты
                # (например, при горизонтально объединенных ячейках Истории)
                sig = (
                    lesson_info.get("subject"),
                    lesson_info.get("teacher"),
                    lesson_info.get("classroom"),
                )

                already_exists = False
                for entry in day_entries[day]:
                    if entry["subrow_idx"] == subrow_idx and entry["sig"] == sig:
                        already_exists = True
                        break

                if not already_exists:
                    day_entries[day].append(
                        {"subrow_idx": subrow_idx, "sig": sig, "info": lesson_info}
                    )

        # Записываем найденные уроки в словарь расписания
        # Записываем найденные уроки в словарь расписания
        for day, entries in day_entries.items():
            if lesson_num == "0":
                if entries:
                    schedules[group_name]["zero_lesson"][day] = entries[0]["info"]
                continue

            # Сценарий 1: У пары всего 1 физическая строка в таблице (нет деления по вертикали)
            if num_subrows == 1:
                if len(entries) == 1:
                    schedules[group_name]["days"][day][lesson_num] = entries[0]["info"]
                else:
                    # Разные предметы в одной строке (подгруппы) -> 1.1, 1.2
                    for i, entry in enumerate(entries, 1):
                        lesson_key = f"{lesson_num}.{i}"
                        schedules[group_name]["days"][day][lesson_key] = entry["info"]

            # Сценарий 2: У пары несколько строк (2 или более) - потенциальные половинки
            else:
                # 1. Группируем записи по физическим строкам (половинкам)
                entries_by_subrow = defaultdict(list)
                for e in entries:
                    entries_by_subrow[e["subrow_idx"]].append(e)

                # 2. Проверка: если одна сигнатура занимает ВСЕ строки пары (целая пара)
                sig_counts = defaultdict(int)
                for e in entries:
                    sig_counts[e["sig"]] += 1

                full_lesson_info = None
                for sig, count in sig_counts.items():
                    if count == num_subrows:
                        # Находим объект инфо для этой сигнатуры
                        full_lesson_info = next(
                            e["info"] for e in entries if e["sig"] == sig
                        )
                        break

                if full_lesson_info:
                    # Если предмет дублируется во всех строках — записываем как целую пару
                    schedules[group_name]["days"][day][lesson_num] = full_lesson_info
                else:
                    # 3. Иначе обрабатываем каждую половинку отдельно
                    for sub_idx in range(num_subrows):
                        row_entries = entries_by_subrow.get(sub_idx, [])
                        if not row_entries:
                            continue

                        # Базовый ключ для половинки: 1.1, 1.2
                        base_key = f"{lesson_num}.{sub_idx + 1}"

                        if len(row_entries) == 1:
                            # Одна запись в половинке (например, общая пара в первой половине)
                            schedules[group_name]["days"][day][base_key] = row_entries[
                                0
                            ]["info"]
                        else:
                            # Несколько записей в одной половинке (подгруппы) -> 1.2.1, 1.2.2
                            for sub_grp_idx, e in enumerate(row_entries, 1):
                                key = f"{base_key}.{sub_grp_idx}"
                                schedules[group_name]["days"][day][key] = e["info"]

    print(f"--- [ТАБЛИЦА] Конец парсинга для группы: {group_name} ---\n")


def parse_schedule_from_docx(file_path: str):
    """Парсит расписание из DOCX и возвращает dict с логом"""
    print(f"=== [DOCX] Открытие файла: {file_path} ===")
    doc = Document(file_path)
    schedules = {}
    current_group = None

    # 1. Поиск групп в параграфах
    print(f"[DOCX] Всего параграфов: {len(doc.paragraphs)}")
    for p_idx, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue

        group_match = re.search(r"Расписание уроков\s+для\s+(.+?)\s+группы", text)
        if group_match:
            current_group = group_match.group(1).strip()
            print(f"[ГРУППА] Найдена группа '{current_group}' в параграфе {p_idx}")
            schedules[current_group] = {
                "days": {day: {} for day in days_ru},
                "zero_lesson": {day: {} for day in days_ru},
            }
            continue

    print(f"[DOCX] Итого найдено групп: {list(schedules.keys())}")

    # 2. Поиск таблиц
    # Фильтруем таблицы, где есть упоминание дней недели
    tables = []
    for t_idx, t in enumerate(doc.tables):
        # Собираем весь текст таблицы для проверки
        table_text = "\n".join(cell.text for row in t.rows for cell in row.cells)
        has_day = any(day in table_text for day in days_ru)
        if has_day:
            tables.append(t)
            print(
                f"[ТАБЛИЦА #{t_idx}] Найдена таблица с расписанием (содержит дни недели)"
            )
        else:
            # print(f"[ТАБЛИЦА #{t_idx}] Пропущена (нет дней недели)")
            pass

    print(f"[DOCX] Найдено подходящих таблиц: {len(tables)}")

    # 3. Сопоставление таблиц и групп
    group_index = 0
    group_names = list(schedules.keys())

    print(f"[СОПОСТАВЛЕНИЕ] Групп: {len(group_names)}, Таблиц: {len(tables)}")

    for table_idx, table in enumerate(tables):
        if group_index >= len(group_names):
            print(
                f"[ОШИБКА] Таблиц больше, чем групп. Остановка на таблице {table_idx}"
            )
            break

        group_name = group_names[group_index]
        print(f"[СОПОСТАВЛЕНИЕ] Таблица #{table_idx} -> Группа '{group_name}'")

        parse_schedule_table_fixed(table, group_name, schedules)
        group_index += 1

    print("=== [DOCX] Парсинг завершен ===")
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
                    normalized[k] = {
                        "shift": v.get("shift", 1),
                        "room": v.get("room", ""),
                    }
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

        await db.schedules.insert_one(
            {
                "group_name": group,
                "schedule": schedule_with_classrooms,
                "shift_info": shifts.get(group, {"shift": 1}),
                "updated_at": datetime.now(),
            }
        )
    print(f"✅ Залито расписание для {len(data)} групп в MongoDB")
