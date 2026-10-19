from datetime import datetime
from fastapi import HTTPException, UploadFile
from app.database import db
from app.models.schedule import Schedule
from app.models.schedule_upload import UploadResponse
from app.models.teacher_schedule import (
    ParallelLesson,
    TeacherLesson,
    TeacherScheduleResponse,
    TeacherShiftSchedule,
)
from app.services.schedule_parser import (
    add_classrooms_to_schedule,
    load_group_shifts,
    parse_schedule_from_docx,
)
from app.utils.common import normalize_day_name, normalize_name, serialize_doc
from app.services.bell_service import apply_bell_schedule_from_files


class ScheduleService:
    @staticmethod
    def sort_lessons_by_number(lessons: dict) -> dict:
        """Сортирует пары по номерам: 1, 2, 2.1, 2.2, 3, 4, 4.1, 4.2"""
        if not lessons:
            return lessons

        def parse_key(key):
            # "4" → (4, 0)
            # "4.2" → (4, 2)
            try:
                if "." in key:
                    parts = key.split(".")
                    return int(parts[0]), int(parts[1])
                return int(key), 0
            except (ValueError, IndexError):
                # Если ключ не число (на всякий случай), отправляем в конец
                return 999, 0

        sorted_keys = sorted(lessons.keys(), key=parse_key)
        return {k: lessons[k] for k in sorted_keys}

    @staticmethod
    def _put_lesson(
        day_slots: dict,
        num: str,
        group_name: str,
        subject: str,
        classroom,
        time,
    ) -> None:
        """Кладёт пару преподавателя в слот дня, не теряя параллельные группы.

        Раньше здесь была прямая запись `day_slots[num] = TeacherLesson(...)`.
        Если преподаватель ведёт одну и ту же пару у нескольких групп, каждая
        следующая группа ЗАТИРАЛА предыдущую — в ответе оставалась одна группа
        из многих (у Мымриной Ю.Г. на «Разговорах о важном» оставалась 1 группа
        из 8).

        Теперь первая встреченная группа становится основной парой, остальные —
        её `parallels`.

        Приоритет выбора основной пары: нулевая пара -> младший номер пары ->
        алфавит по группе. Порядок обхода `db.schedules` не гарантирован, поэтому
        выбор не должен зависеть от того, чей документ попался первым.

        Идемпотентно: ту же группу повторно не добавит.
        """
        if num not in day_slots:
            day_slots[num] = TeacherLesson(
                subject=subject,
                group=group_name,
                classroom=classroom,
                time=time,
            )
            return

        current = day_slots[num]

        # та же группа — ничего не делаем (повторный документ в базе)
        if current.group == group_name:
            return

        # У нулевой пары предмет и время обычно пустые у всех групп. Если
        # основная пара «пустее» новой — берём новую как основную, а прежнюю
        # уводим в параллели, чтобы в ответе не оказалось пустого предмета.
        if not (current.subject or "").strip() and (subject or "").strip():
            day_slots[num] = TeacherLesson(
                subject=subject,
                group=group_name,
                classroom=classroom,
                time=time,
                parallels=[
                    ParallelLesson(
                        subject=current.subject,
                        group=current.group,
                        classroom=current.classroom,
                    ),
                    *current.parallels,
                ],
            )
            return

        # обычный случай — уводим группу в параллели
        parallel = ParallelLesson(
            subject=subject, group=group_name, classroom=classroom
        )
        for existing in current.parallels:
            if existing.group == group_name:
                return  # уже есть
        current.parallels.append(parallel)

        # у основной пары должно быть время; у параллелей его нет
        if not current.time and time:
            current.time = time

    @staticmethod
    async def get_all_schedules():
        schedules = await db.schedules.find().to_list(100)
        return [Schedule(**serialize_doc(s)) for s in schedules]

    @staticmethod
    async def get_schedule_by_group(group_name: str, day: str | None):
        """
        Возвращает расписание группы.
        Ошибки:
        - 404: группа не найдена;
        - 204/404: группа есть, но в этот день нет пар.
        """
        schedule = await db.schedules.find_one({"group_name": group_name})
        if not schedule:
            raise HTTPException(
                status_code=404, detail=f"Группа '{group_name}' не найдена"
            )

        schedule = serialize_doc(schedule)
        shift_info = schedule.get("shift_info", {})

        if not day:
            # Если день не указан, возвращаем всё расписание (тоже отсортированное)
            full_schedule = schedule.get("schedule", {})
            # Сортируем дни внутри расписания
            if "days" in full_schedule:
                sorted_days = {}
                for dname, lessons in full_schedule["days"].items():
                    sorted_days[dname] = ScheduleService.sort_lessons_by_number(lessons)
                full_schedule["days"] = sorted_days

            return Schedule(
                group_name=group_name,
                shift_info=shift_info,
                updated_at=schedule.get("updated_at"),
                schedule=full_schedule,
            )

        normalized_day = normalize_day_name(day)
        schedule_data = schedule.get("schedule", {})
        filtered_schedule = {}

        # фильтрация
        zero = schedule_data.get("zero_lesson", {})
        for dname, info in zero.items():
            if normalize_day_name(dname) == normalized_day:
                filtered_schedule["zero_lesson"] = {dname: info}

        days = schedule_data.get("days", {})
        for dname, lessons in days.items():
            if normalize_day_name(dname) == normalized_day:
                # === ИСПРАВЛЕНИЕ: Сортируем пары перед возвратом ===
                sorted_lessons = ScheduleService.sort_lessons_by_number(lessons)
                filtered_schedule["days"] = {dname: sorted_lessons}
                break

        # ❗ Если группа есть, но в этот день нет пар
        if not filtered_schedule:
            raise HTTPException(
                status_code=404,
                detail=f"У группы '{group_name}' нет занятий в день '{day}'",
            )

        return {
            "group_name": group_name,
            "shift_info": shift_info,
            "day": day,
            "schedule": filtered_schedule,
            "updated_at": schedule.get("updated_at"),
        }

    @staticmethod
    async def upload_schedule(
        schedule_file: UploadFile, shifts_file: UploadFile | None
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
                raise HTTPException(
                    status_code=400, detail="Не удалось распарсить расписание"
                )

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
                schedule_with_classrooms = add_classrooms_to_schedule(
                    schedule, group, shifts
                )

                doc = {
                    "group_name": group,
                    "schedule": schedule_with_classrooms,
                    "shift_info": shift_info,
                    "updated_at": datetime.now(),
                }
                result = await db.schedules.insert_one(doc)
                inserted.append(str(result.inserted_id))

            await apply_bell_schedule_from_files()

            return UploadResponse(
                message=(
                    f"✅ Расписание загружено для {len(inserted)} групп "
                    f"({first_shift_count} — 1 смена, {second_shift_count} — 2 смена)"
                ),
                inserted_ids=inserted,
                total_groups=len(inserted),
                first_shift=first_shift_count,
                second_shift=second_shift_count,
            )

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Ошибка при обработке файлов: {e}"
            )

        finally:
            for path in [temp_docx, temp_json]:
                if path and os.path.exists(path):
                    os.remove(path)

    @staticmethod
    async def delete_schedule(group_name: str):
        result = await db.schedules.delete_one({"group_name": group_name})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"message": f"Schedule for '{group_name}' deleted"}

    @staticmethod
    async def get_teacher_schedule(fio: str, day: str | None):
        """
        Гибкий поиск расписания преподавателя.
        Теперь возвращает разные ошибки:
        - 404: преподаватель не найден вообще;
        - 204: преподаватель найден, но в указанный день у него нет пар.
        """
        fio = fio.strip()
        if not fio:
            raise HTTPException(
                status_code=400, detail="Некорректное ФИО преподавателя"
            )

        fio_normalized = normalize_name(fio)
        normalized_day = normalize_day_name(day) if day else None

        schedules = await db.schedules.find().to_list(1000)

        # Устойчивый порядок обхода. Раньше он зависел от порядка документов в
        # Mongo, поэтому «основной» группой в параллели становилась случайная
        # (и менялась от запроса к запросу). Сортируем по имени группы, чтобы
        # одна и та же пара всегда отдавалась одинаково.
        def _group_sort_key(doc):
            name = (doc.get("group_name") or "").strip()
            head = ""
            for ch in name:
                if ch.isdigit():
                    head += ch
                else:
                    break
            return (int(head) if head else 999, name.casefold())

        schedules = sorted(schedules, key=_group_sort_key)

        teacher_found_anywhere = False
        first_shift: dict[str, dict] = {}
        second_shift: dict[str, dict] = {}

        def coerce_shift(v):
            if v is None or v == "":
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        def pick_shift_target(raw_shift_value):
            shift_value = coerce_shift(raw_shift_value)

            if shift_value == 0:
                return None  # скрытая группа

            if shift_value is None:
                shift_value = 1  # дефолт — 1-я смена

            if shift_value == 1:
                return first_shift
            if shift_value == 2:
                return second_shift

            return None

        # --- Поиск занятий преподавателя с учетом смен ---
        for s in schedules:
            group_name = s.get("group_name")
            schedule_data = s.get("schedule", {})
            if not schedule_data:
                continue

            raw_shift = (s.get("shift_info") or {}).get("shift", 1)
            shift_target = pick_shift_target(raw_shift)
            if shift_target is None:
                continue

            def match_teacher(teacher: str) -> bool:
                if not teacher:
                    return False
                t_norm = normalize_name(teacher)
                return fio_normalized in t_norm or t_norm in fio_normalized

            # нулевая пара
            for day_name, zero in (schedule_data.get("zero_lesson") or {}).items():
                if zero and match_teacher(zero.get("teacher", "")):
                    teacher_found_anywhere = True
                    if (
                        not normalized_day
                        or normalize_day_name(day_name) == normalized_day
                    ):
                        shift_target.setdefault(day_name, {})
                        ScheduleService._put_lesson(
                            shift_target[day_name],
                            "0",
                            group_name,
                            zero.get("subject", ""),
                            zero.get("classroom", ""),
                            zero.get("time"),
                        )

            # обычные пары
            for day_name, lessons in (schedule_data.get("days") or {}).items():
                for num, info in (lessons or {}).items():
                    if info and match_teacher(info.get("teacher", "")):
                        teacher_found_anywhere = True
                        if (
                            not normalized_day
                            or normalize_day_name(day_name) == normalized_day
                        ):
                            shift_target.setdefault(day_name, {})
                            ScheduleService._put_lesson(
                                shift_target[day_name],
                                num,
                                group_name,
                                info.get("subject", ""),
                                info.get("classroom", ""),
                                info.get("time"),
                            )

        if not teacher_found_anywhere:
            raise HTTPException(
                status_code=404, detail=f"Преподаватель '{fio}' не найден в расписании"
            )

        # Если фильтровали по дню и ничего не попало в обе смены:
        if (
            normalized_day
            and not any(first_shift.values())
            and not any(second_shift.values())
        ):
            raise HTTPException(
                status_code=204,
                detail=f"У преподавателя '{fio}' нет пар в день '{day}'",
            )

        # === ИСПРАВЛЕНИЕ: Сортируем пары в расписании преподавателя ===
        for day_name in first_shift:
            first_shift[day_name] = ScheduleService.sort_lessons_by_number(
                first_shift[day_name]
            )

        for day_name in second_shift:
            second_shift[day_name] = ScheduleService.sort_lessons_by_number(
                second_shift[day_name]
            )

        return TeacherScheduleResponse(
            teacher_fio=fio,
            filtered_by_day=day if day else None,
            schedule=TeacherShiftSchedule(
                first_shift=first_shift, second_shift=second_shift
            ),
        )
