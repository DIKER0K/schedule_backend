from datetime import datetime
from fastapi import HTTPException, UploadFile
from app.database import db
from app.models.schedule import Schedule
from app.models.schedule_upload import UploadResponse
from app.models.teacher_schedule import TeacherScheduleResponse
from app.services.schedule_parser import add_classrooms_to_schedule, load_group_shifts, parse_schedule_from_docx
from app.utils.common import normalize_day_name, normalize_name, serialize_doc

class ScheduleService:
    
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
            raise HTTPException(status_code=404, detail=f"Группа '{group_name}' не найдена")

        schedule = serialize_doc(schedule)
        shift_info = schedule.get("shift_info", {})

        if not day:
            return Schedule(
                group_name=group_name,
                shift_info=shift_info,
                updated_at=schedule.get("updated_at"),
                schedule=schedule.get("schedule", {})
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
                filtered_schedule["days"] = {dname: lessons}
                break

        # ❗ Если группа есть, но в этот день нет пар
        if not filtered_schedule:
            raise HTTPException(
                status_code=404,
                detail=f"У группы '{group_name}' нет занятий в день '{day}'"
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
        schedule_file: UploadFile,shifts_file: UploadFile | None):
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

            return UploadResponse(
                message=(
                    f"✅ Расписание загружено для {len(inserted)} групп "
                    f"({first_shift_count} — 1 смена, {second_shift_count} — 2 смена)"
                ),
                inserted_ids=inserted,
                total_groups=len(inserted),
                first_shift=first_shift_count,
                second_shift=second_shift_count
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка при обработке файлов: {e}")

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
    async def get_teacher_schedule(fio: str,day: str | None):
        """
        Гибкий поиск расписания преподавателя.
        Теперь возвращает разные ошибки:
        - 404: преподаватель не найден вообще;
        - 204: преподаватель найден, но в указанный день у него нет пар.
        """
        fio = fio.strip()
        if not fio:
            raise HTTPException(status_code=400, detail="Некорректное ФИО преподавателя")

        fio_normalized = normalize_name(fio)
        normalized_day = normalize_day_name(day) if day else None

        schedules = await db.schedules.find().to_list(1000)
        teacher_found_anywhere = False  # 👈 отметим, что преподаватель вообще существует
        teacher_schedule = {"first_shift": {}, "second_shift": {}}

        for s in schedules:
            group_name = s.get("group_name")
            schedule_data = s.get("schedule", {})
            if not schedule_data:
                continue

            shift = (s.get("shift_info") or {}).get("shift", 1)
            shift_key = "first_shift" if shift == 1 else "second_shift"

            def match_teacher(teacher: str) -> bool:
                if not teacher:
                    return False
                t_norm = normalize_name(teacher)
                return fio_normalized in t_norm or t_norm in fio_normalized

            # нулевая пара
            for day_name, zero in (schedule_data.get("zero_lesson") or {}).items():
                if zero and match_teacher(zero.get("teacher", "")):
                    teacher_found_anywhere = True
                    if not normalized_day or normalize_day_name(day_name) == normalized_day:
                        teacher_schedule[shift_key].setdefault(day_name, {})
                        teacher_schedule[shift_key][day_name]["0"] = {
                            "subject": zero.get("subject", ""),
                            "group": group_name,
                            "classroom": zero.get("classroom", "")
                        }

            # обычные пары
            for day_name, lessons in (schedule_data.get("days") or {}).items():
                for num, info in (lessons or {}).items():
                    if info and match_teacher(info.get("teacher", "")):
                        teacher_found_anywhere = True
                        if not normalized_day or normalize_day_name(day_name) == normalized_day:
                            teacher_schedule[shift_key].setdefault(day_name, {})
                            teacher_schedule[shift_key][day_name][num] = {
                                "subject": info.get("subject", ""),
                                "group": group_name,
                                "classroom": info.get("classroom", "")
                            }

        # ❌ Если вообще не найден преподаватель
        if not teacher_found_anywhere:
            raise HTTPException(status_code=404, detail=f"Преподаватель '{fio}' не найден в расписании")

        # ❗ Если преподаватель найден, но в этот день нет пар
        if normalized_day and not any(teacher_schedule["first_shift"].values()) and not any(teacher_schedule["second_shift"].values()):
            raise HTTPException(status_code=404, detail=f"У преподавателя '{fio}' нет пар в день '{day}'")

        # ✅ Всё хорошо
        return TeacherScheduleResponse(
            teacher_fio=fio,
            filtered_by_day=day if day else None,
            schedule=teacher_schedule
        )