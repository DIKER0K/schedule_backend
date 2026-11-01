import copy
from datetime import date, datetime, timedelta
from fastapi import HTTPException, UploadFile
from app.database import db
from app.models.schedule import Schedule
from app.models.schedule_upload import ClassroomUploadResponse, UploadResponse
from app.models.teacher_schedule import (
    ParallelLesson,
    TeacherLesson,
    TeacherScheduleResponse,
    TeacherShiftSchedule,
)
from app.services.schedule_parser import (
    add_classrooms_to_schedule,
    load_group_shifts,
    parse_schedule_document,
)
from app.utils.common import normalize_day_name, normalize_name
from app.services.bell_service import apply_bell_schedule_from_files
from app.services.holidays_service import fmt_day_month, holiday_on
from app.services.classroom_distribution import (
    distribution_on_date,
    distribution_period,
    group_key,
    parse_distribution_document,
    versions_for_period,
)
from app.services.next_bell_service import (
    TZ,
    WEEKDAY_TO_BELL_KEY,
    WEEKDAY_TO_RU,
    _date_note,
    _date_overrides,
    bell_grid_for,
)
from app.services.schedule_versions import (
    applicable_versions,
    coerce_date,
    fmt_date,
    latest_per_group,
    merge_week,
    mongo_date,
    parse_requested_date,
    resolve_day,
    today,
    version_end,
)


def group_sort_key(doc: dict) -> tuple:
    """Порядок групп в выдаче: по числу в начале названия, затем по алфавиту."""
    name = (doc.get("group_name") or "").strip()
    head = ""
    for char in name:
        if char.isdigit():
            head += char
        else:
            break
    return (int(head) if head else 999, name.casefold())


def iter_schedule_lessons(schedule_data: dict):
    """Пары расписания по одной: (день недели, номер пары, данные пары).

    Нулевая пара идёт с номером «0» — так же, как она приходит в ответе
    преподавателю.
    """
    for day_name, zero in (schedule_data.get("zero_lesson") or {}).items():
        if zero:
            yield day_name, "0", zero
    for day_name, lessons in (schedule_data.get("days") or {}).items():
        for num, info in (lessons or {}).items():
            if info:
                yield day_name, str(num), info


def day_in_week(day_name: str, week_start: date) -> date | None:
    """Дата дня недели внутри недели, начинающейся с `week_start` (понедельник)."""
    normalized = normalize_day_name(day_name)
    for index, name in WEEKDAY_TO_RU.items():
        if normalize_day_name(name) == normalized:
            return week_start + timedelta(days=index)
    return None


def coerce_shift(value) -> int | None:
    """Номер смены из базы или запроса. Пустое значение — None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def lesson_place(info: dict, distribution: dict) -> tuple:
    """Кабинет пары: свой кабинет → кабинет группы из версии → пометка.

    В приказе на период кабинет закреплён за ГРУППОЙ (`classroom` версии), а в
    самих парах его нет — без подстановки у преподавателя все пары оставались
    без кабинетов. Если вместо номера в приказе пометка «ПП» / «ДО», она
    показывается вместо кабинета: у этих групп пары идут на практике или
    дистанционно.
    """
    classroom = str(info.get("classroom") or "").strip()
    if classroom:
        return classroom, None

    group_classroom = str((distribution or {}).get("classroom") or "").strip()
    if group_classroom:
        return group_classroom, None

    note = str((distribution or {}).get("classroom_note") or "").strip()
    return None, (note.upper() or None)



def today_bells(shift: int = 1, at: str | None = None):
    """Звонки на сегодня по разовому правилу: ({номер пары: "HH:MM–HH:MM"}, объявление).

    Разовое правило на дату (приказ администрации) задаёт полную сетку дня,
    поэтому его сетка используется как есть. Если правила на сегодня нет —
    возвращаются пустые значения и расписание остаётся обычным.
    """
    from datetime import datetime as _dt

    now = _dt.fromisoformat(at) if at else _dt.now(TZ)
    date_key = now.strftime("%Y-%m-%d")
    dated = (_date_overrides().get(date_key) or {}).get(f"{shift}_shift") or {}
    grid = {
        str(number): str(span)
        for number, span in dated.items()
        if str(number) not in ("note", "source")
    }
    return grid, _date_note(date_key)


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
        classroom_note=None,
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
                classroom_note=classroom_note,
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
                classroom_note=classroom_note,
                time=time,
                parallels=[
                    ParallelLesson(
                        subject=current.subject,
                        group=current.group,
                        classroom=current.classroom,
                        classroom_note=current.classroom_note,
                    ),
                    *current.parallels,
                ],
            )
            return

        # обычный случай — уводим группу в параллели
        parallel = ParallelLesson(
            subject=subject,
            group=group_name,
            classroom=classroom,
            classroom_note=classroom_note,
        )
        for existing in current.parallels:
            if existing.group == group_name:
                return  # уже есть
        current.parallels.append(parallel)

        # у основной пары должно быть время; у параллелей его нет
        if not current.time and time:
            current.time = time

    @staticmethod
    def apply_today_bells(schedule_data: dict, shift: int, at: str | None = None):
        """Подставляет сегодняшние звонки в расписание, если на дату есть приказ.

        Приказ администрации («2 ноября звонки такие») меняет сетку дня целиком,
        а время пар в базе привязано к обычному дню недели. Поэтому на выдаче
        подменяем время пар сегодняшнего дня, не трогая базу: завтра расписание
        снова обычное.
        """
        grid, note = today_bells(shift, at)
        if not grid:
            return schedule_data, note

        # Свой экземпляр: подмена не должна менять документ вызывающего
        schedule_data = copy.deepcopy(schedule_data)

        moment = (
            datetime.fromisoformat(at).replace(tzinfo=TZ)
            if at
            else datetime.now(TZ)
        )
        today_name = WEEKDAY_TO_RU[moment.weekday()]
        days = schedule_data.get("days") or {}
        for dname, lessons in days.items():
            if normalize_day_name(dname) != normalize_day_name(today_name):
                continue
            for number, lesson in lessons.items():
                if not isinstance(lesson, dict):
                    continue
                span = grid.get(str(number).split(".")[0].strip())
                if span:
                    lesson["time"] = span
        return schedule_data, note

    @staticmethod
    def apply_shift_bell_grid(schedule_data: dict, shift: int, day_date: date):
        """Время пар по сетке звонков своей смены на дату.

        В базе время пар проставлено при загрузке по смене группы из
        `group_shifts.json`. У версии смена своя: 2 и 3 ноября часть групп
        учится во вторую смену, и время пар у них считается по сетке второй
        смены на эту дату — обычной или разовой из приказа. Обратный случай
        тоже возможен (группа постоянно во вторую смену, а приказ вернул её в
        первую), поэтому сетка смены применяется в обе стороны.
        """
        grid = bell_grid_for(day_date, shift)
        if not grid:
            return schedule_data

        day_name = WEEKDAY_TO_RU[day_date.weekday()]
        normalized = normalize_day_name(day_name)
        schedule_data = copy.deepcopy(schedule_data)

        for section in ("days", "zero_lesson"):
            for dname, lessons in (schedule_data.get(section) or {}).items():
                if normalize_day_name(dname) != normalized:
                    continue
                for number, lesson in (lessons or {}).items():
                    if not isinstance(lesson, dict):
                        continue
                    number_key = str(number).strip()
                    span = grid.get(number_key) or grid.get(
                        number_key.split(".")[0].strip()
                    )
                    if span:
                        lesson["time"] = span
        return schedule_data

    @staticmethod
    def apply_week_bell_grid(schedule_data: dict, week_start: date, shift_on):
        """Время пар по сетке звонков смены группы на каждый день недели.

        `shift_on(дата)` возвращает номер смены группы на дату: по приказу на
        период группа может уехать во вторую смену, и тогда время пар берётся
        из сетки второй смены на этот день, а не из той, что проставлена при
        загрузке расписания. День недели без даты (например, воскресенье)
        остаётся как есть.
        """
        result = schedule_data
        for offset in range(6):  # понедельник–суббота: в воскресенье пар нет
            day = week_start + timedelta(days=offset)
            shift = shift_on(day)
            if shift not in (1, 2):
                continue
            result = ScheduleService.apply_shift_bell_grid(result, shift, day)
        return result

    @staticmethod
    async def get_all_schedules():
        """Все группы по одной записи: версия расписания, действующая сегодня."""
        docs = await db.schedules.find().to_list(None)
        day = today()
        by_group: dict[str, list] = {}
        for doc in docs:
            by_group.setdefault(doc.get("group_name"), []).append(doc)

        current = sorted(latest_per_group(docs, day), key=group_sort_key)
        result = []
        for doc in current:
            # Распределение ищем по всем версиям группы: приказ мог остаться в
            # версии, которую более поздняя загрузка расписания уже сменила.
            distribution = distribution_on_date(
                by_group.get(doc.get("group_name"), [doc]), day
            )
            shift_info = doc.get("shift_info") or {}
            result.append(
                Schedule(
                    group_name=doc.get("group_name"),
                    shift_info=shift_info,
                    shift=distribution["shift"]
                    or int(shift_info.get("shift") or 1),
                    classroom=distribution["classroom"],
                    building=distribution["building"],
                    classroom_note=distribution["classroom_note"],
                    updated_at=doc.get("updated_at"),
                    schedule=doc.get("schedule") or {},
                    valid_from=coerce_date(doc.get("valid_from")),
                    valid_to=version_end(doc),
                    requested_date=day,
                )
            )
        return result

    @staticmethod
    async def get_schedule_by_group(
        group_name: str, day: str | None, date_value: date | None = None
    ):
        """
        Возвращает расписание группы на дату (по умолчанию — сегодня).

        Версия выбирается по дате: самая поздняя, которая уже началась
        (`valid_from <= дата`). Если в ней запрошенного дня нет пар, день берётся
        из предыдущей версии — так новый приказ на 2–3 ноября не стирает
        остальную неделю.

        Ошибки:
        - 404: группы нет в базе;
        - 404: дата — нерабочий день (приказ «4 ноября — выходной»);
        - 404: расписание на дату ещё не опубликовано (все версии начинаются
          позже либо нужного дня нет ни в одной версии).
        """
        day_date = date_value or today()
        docs = await db.schedules.find({"group_name": group_name}).to_list(None)
        if not docs:
            raise HTTPException(
                status_code=404, detail=f"Группа '{group_name}' не найдена"
            )

        # Нерабочий день важнее вопроса «опубликовано ли расписание»: занятий
        # в этот день нет по приказу, а не потому, что версию не загрузили.
        if await holiday_on(day_date):
            raise HTTPException(
                status_code=404,
                detail=f"{fmt_day_month(day_date)} — выходной, занятий нет",
            )

        versions = applicable_versions(docs, day_date)
        if not versions:
            raise HTTPException(
                status_code=404,
                detail=f"Расписание на {fmt_date(day_date)} ещё не опубликовано",
            )

        newest = versions[0]
        shift_info = newest.get("shift_info") or {}
        # Смена и кабинет — из версии, действующей на запрошенную дату: на 2–3
        # ноября часть групп переезжает во вторую смену, дальше — обычная.
        # Смотрим все версии группы: приказ мог остаться в той, которую уже
        # сменила более поздняя загрузка расписания.
        distribution = distribution_on_date(docs, day_date)
        shift = distribution["shift"] or int(shift_info.get("shift") or 1)
        # Тот же момент, что и запрошенная дата: время пар подменяется по приказу
        # на эту дату, а не на сегодняшнюю.
        at = f"{day_date.isoformat()}T12:00"

        if not day:
            full_schedule = merge_week(versions)
            for dname, lessons in full_schedule["days"].items():
                full_schedule["days"][dname] = ScheduleService.sort_lessons_by_number(
                    lessons
                )

            full_schedule, day_note = ScheduleService.apply_today_bells(
                full_schedule, shift, at
            )
            full_schedule = ScheduleService.apply_shift_bell_grid(
                full_schedule, shift, day_date
            )
            return Schedule(
                group_name=group_name,
                shift_info=shift_info,
                shift=shift,
                classroom=distribution["classroom"],
                building=distribution["building"],
                classroom_note=distribution["classroom_note"],
                updated_at=newest.get("updated_at"),
                schedule=full_schedule,
                valid_from=coerce_date(newest.get("valid_from")),
                valid_to=version_end(newest),
                requested_date=day_date,
                note=day_note,
            )

        resolved = resolve_day(versions, day)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Расписание на {fmt_date(day_date)} ещё не опубликовано: "
                    f"в день '{day}' пар нет ни в одной версии"
                ),
            )

        source, filtered_schedule = resolved
        for dname, lessons in filtered_schedule.get("days", {}).items():
            filtered_schedule["days"][dname] = ScheduleService.sort_lessons_by_number(
                lessons
            )

        filtered_schedule, day_note = ScheduleService.apply_today_bells(
            filtered_schedule, shift, at
        )
        filtered_schedule = ScheduleService.apply_shift_bell_grid(
            filtered_schedule, shift, day_date
        )
        return {
            "group_name": group_name,
            "shift_info": shift_info,
            "shift": shift,
            "classroom": distribution["classroom"],
            "building": distribution["building"],
            "classroom_note": distribution["classroom_note"],
            "day": day,
            "schedule": filtered_schedule,
            "updated_at": source.get("updated_at"),
            "note": day_note,
            "valid_from": coerce_date(source.get("valid_from")),
            "valid_to": version_end(source),
            "requested_date": day_date,
        }

    @staticmethod
    async def upload_schedule(
        schedule_file: UploadFile,
        shifts_file: UploadFile | None,
        valid_from: str | None = None,
        replace_all: bool = False,
    ):
        """
        Загружает файл .docx, парсит его и записывает как ВЕРСИЮ расписания.
        Если передан также файл group_shifts.json — он обновляется вместе с расписанием.

        Дата начала версии берётся из заголовка документа
        («Расписание уроков для 9-1 группы с 02.11.2026 г.»), а для групп без
        даты в заголовке — из параметра `valid_from`. Версия пишется по паре
        (group_name, valid_from): повторная загрузка файла с той же датой
        заменяет её, остальные версии остаются. `replace_all=True` сносит
        прежние версии групп, которые есть в файле.
        """
        import os, json

        if not schedule_file.filename.lower().endswith(".docx"):
            raise HTTPException(status_code=400, detail="Нужен DOCX файл расписания")

        try:
            request_date = parse_requested_date(valid_from)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Не удалось разобрать valid_from: {exc}"
            )

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
            data, title_dates = parse_schedule_document(temp_docx)
            if not data:
                raise HTTPException(
                    status_code=400, detail="Не удалось распарсить расписание"
                )

            # дата начала по каждой группе
            starts = {}
            without_date = []
            for group in data:
                group_start = title_dates.get(group) or request_date
                if group_start is None:
                    without_date.append(group)
                else:
                    starts[group] = group_start

            if without_date:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Не удалось определить дату начала: укажите valid_from "
                        "(в заголовке файла нет даты для групп: "
                        f"{', '.join(sorted(without_date))})"
                    ),
                )

            # загружаем смены (уже обновлённые)
            shifts = load_group_shifts()

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

                start = starts[group]
                stored_start = mongo_date(start)
                doc = {
                    "group_name": group,
                    "schedule": schedule_with_classrooms,
                    "shift_info": shift_info,
                    "updated_at": datetime.now(),
                    "valid_from": stored_start,
                    "valid_to": None,
                }

                if replace_all:
                    # файл действительно полный: прежние версии группы не нужны
                    await db.schedules.delete_many({"group_name": group})
                    result = await db.schedules.insert_one(doc)
                    inserted.append(str(result.inserted_id))
                    continue

                result = await db.schedules.update_one(
                    {"group_name": group, "valid_from": stored_start},
                    {"$set": doc},
                    upsert=True,
                )
                if result.upserted_id is not None:
                    inserted.append(str(result.upserted_id))
                else:
                    existing = await db.schedules.find_one(
                        {"group_name": group, "valid_from": stored_start}
                    )
                    if existing:
                        inserted.append(str(existing["_id"]))

            await apply_bell_schedule_from_files()

            dates = sorted({start for start in starts.values()})
            dates_text = (
                fmt_date(dates[0])
                if len(dates) == 1
                else " и ".join(fmt_date(d) for d in dates)
            )
            message = (
                f"✅ Расписание с {dates_text} загружено для {len(inserted)} групп "
                f"({first_shift_count} — 1 смена, {second_shift_count} — 2 смена)"
            )
            if replace_all:
                message += ". Прежние версии этих групп удалены"

            total_versions = await db.schedules.count_documents({})

            return UploadResponse(
                message=message,
                inserted_ids=inserted,
                total_groups=len(inserted),
                first_shift=first_shift_count,
                second_shift=second_shift_count,
                valid_from=dates[0] if len(dates) == 1 else None,
                total_versions=total_versions,
                replace_all=replace_all,
                dates={group: start.isoformat() for group, start in starts.items()},
            )

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Ошибка при обработке файлов: {e}"
            )

        finally:
            for path in [temp_docx, temp_json]:
                if path and os.path.exists(path):
                    os.remove(path)

    @staticmethod
    async def upload_classrooms(
        file: UploadFile,
        valid_from: str | None = None,
        valid_to: str | None = None,
        building: int | None = None,
    ):
        """Загружает docx «Распределение групп по кабинетам» на период.

        Смена и кабинет записываются в ВЕРСИИ расписания, действующие на этот
        период, вместе с границами периода (`classrooms_valid_from/to`):
        `group_shifts.json` и `shift_info` группы не меняются, а после периода
        группа снова учится в свою обычную смену. Даты берутся из заголовка
        файла («на 2 и 3 ноября 2026»), `valid_from`/`valid_to` — запасной
        вариант, если дат в заголовке нет.

        Ошибки:
        - 400: не docx, ни одной группы в таблице, не разобрать период;
        - 409: расписания на период ещё нет — пустые версии не создаём;
        - 409: на версию уже загружено распределение на другой период —
          приказ на новую неделю должен прийти вместе с расписанием на неё,
          иначе переезд на 5–6 ноября переписал бы данные 2–3 ноября.
        """
        import os

        if not (file.filename or "").lower().endswith(".docx"):
            raise HTTPException(
                status_code=400, detail="Нужен DOCX файл распределения по кабинетам"
            )

        temp_docx = f"temp_classrooms_{datetime.now().timestamp()}.docx"
        try:
            with open(temp_docx, "wb") as handle:
                handle.write(await file.read())

            parsed = parse_distribution_document(temp_docx, fallback_year=today().year)
            groups = parsed["groups"]
            if not groups:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "В файле не найдено ни одной группы: ожидается таблица "
                        "«Группа | Кабинет» с блоками «1 СМЕНА» / «2 СМЕНА»"
                    ),
                )

            try:
                request_from = parse_requested_date(valid_from)
                request_to = parse_requested_date(valid_to)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Не удалось разобрать период: {exc}"
                )

            start = parsed["valid_from"] or request_from
            end = parsed["valid_to"] or request_to or start
            if start is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Не удалось определить период: в заголовке файла нет даты — "
                        "укажите valid_from"
                    ),
                )
            if end is None or end < start:
                raise HTTPException(
                    status_code=400,
                    detail=f"Неверный период: {fmt_date(start)} — {fmt_date(end)}",
                )

            building_value = building if building is not None else parsed["building"]

            docs = await db.schedules.find().to_list(None)
            by_group: dict[str, list] = {}
            for doc in docs:
                by_group.setdefault(doc.get("group_name"), []).append(doc)

            # «2ТД» в приказе и «2 ТД» в расписании — одна и та же группа.
            canonical: dict[str, str] = {}
            for name in sorted(by_group, key=str):
                canonical.setdefault(group_key(name), name)

            # Сначала проверяем всё, потом пишем: иначе половина групп уже
            # обновлена, а запрос упал.
            plans = []
            without_schedule = []
            conflicts = []
            for group, entry in groups.items():
                # «2ТД» в приказе и «2 ТД» в расписании — одна и та же группа.
                name = group
                if name not in by_group:
                    name = canonical.get(group_key(group)) or group

                # Приказ пишется во ВСЕ версии группы, чьё окно действия
                # пересекается с периодом: на 2 и 3 ноября расписание могло
                # прийти двумя файлами, и второй день не должен потерять смену.
                targets = versions_for_period(by_group.get(name, []), start, end)
                if not targets:
                    without_schedule.append(group)
                    continue

                conflict = None
                for target in targets:
                    existing_from, existing_to = distribution_period(target)
                    if existing_from is not None and (
                        existing_from != start or existing_to != end
                    ):
                        conflict = (existing_from, existing_to)
                        break
                if conflict is not None:
                    conflicts.append((group, conflict[0], conflict[1]))
                    continue

                plans.append((group, entry, targets))

            if conflicts:
                group, existing_from, existing_to = conflicts[0]
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "На версию расписания для группы "
                        f"'{group}' уже загружено распределение на "
                        f"{fmt_date(existing_from)} — {fmt_date(existing_to)}. "
                        f"Сначала загрузите расписание на {fmt_date(start)} — "
                        f"{fmt_date(end)}: приказ пишется в версию расписания, "
                        "и на одну версию приходится один период"
                    ),
                )

            updated = 0
            first_shift = 0
            second_shift = 0
            without_classroom: list[str] = []

            for group, entry, targets in plans:
                shift_info = targets[0].get("shift_info") or {}
                shift = entry.get("shift") or int(shift_info.get("shift") or 1)

                fields = {
                    "shift": shift,
                    "classroom": entry.get("classroom"),
                    "classroom_note": entry.get("classroom_note"),
                    "classrooms_valid_from": mongo_date(start),
                    "classrooms_valid_to": mongo_date(end),
                }
                if building_value is not None:
                    fields["building"] = building_value

                for target in targets:
                    await db.schedules.update_one(
                        {"_id": target["_id"]}, {"$set": fields}
                    )
                updated += 1

                if shift == 1:
                    first_shift += 1
                elif shift == 2:
                    second_shift += 1
                if not entry.get("classroom"):
                    without_classroom.append(group)

            if updated == 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Сначала загрузите расписание на этот период "
                        f"({fmt_date(start)} — {fmt_date(end)})"
                    ),
                )

            missing_text = (
                f". Нет расписания на период у {len(without_schedule)} групп: "
                f"{', '.join(sorted(without_schedule)[:10])}"
                if without_schedule
                else ""
            )
            return ClassroomUploadResponse(
                message=(
                    f"✅ Распределение по кабинетам на {fmt_date(start)} — "
                    f"{fmt_date(end)} загружено для {updated} групп "
                    f"({first_shift} — 1 смена, {second_shift} — 2 смена){missing_text}"
                ),
                updated_groups=updated,
                total_groups=len(groups),
                first_shift=first_shift,
                second_shift=second_shift,
                groups_without_classroom=sorted(without_classroom, key=str.casefold),
                groups_without_schedule=sorted(without_schedule, key=str.casefold),
                building=building_value,
                valid_from=start,
                valid_to=end,
            )

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Ошибка при обработке файла: {exc}"
            )

        finally:
            if os.path.exists(temp_docx):
                os.remove(temp_docx)

    @staticmethod
    async def delete_schedule(group_name: str):
        """Удаляет группу совсем — все версии её расписания."""
        result = await db.schedules.delete_many({"group_name": group_name})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {
            "message": f"Schedule for '{group_name}' deleted",
            "deleted_versions": result.deleted_count,
        }

    @staticmethod
    async def get_teacher_schedule(
        fio: str, day: str | None, date_value: date | None = None
    ):
        """Расписание преподавателя на дату (по умолчанию — сегодня).

        Версия расписания выбирается по запрошенной дате, как и у группы: 2–3
        ноября действует приказ (другая смена, кабинеты и время пар), а
        остальные дни продолжают браться из прежней версии. Если в версии,
        действующей на дату, нужного дня нет пар, день берётся из предыдущей
        (`resolve_day`) — иначе новая версия «стирала» бы остальную неделю.

        Время пар — по сетке звонков смены группы на этот день, кабинет — из
        пары, а если его там нет, из версии группы (в приказе кабинет закреплён
        за группой). Пометки «ПП» / «ДО» отдаются вместо номера кабинета.

        Ошибки:
        - 400: пустое ФИО;
        - 404: преподавателя нет ни в одной версии расписания;
        - 404: расписание на дату ещё не опубликовано (у всех групп версии
          начинаются позже);
        - 204: преподаватель есть, но в указанный день у него нет пар.
        """
        fio = fio.strip()
        if not fio:
            raise HTTPException(
                status_code=400, detail="Некорректное ФИО преподавателя"
            )

        fio_normalized = normalize_name(fio)
        normalized_day = normalize_day_name(day) if day else None
        day_date = date_value or today()

        def match_teacher(teacher: str) -> bool:
            if not teacher:
                return False
            t_norm = normalize_name(teacher)
            return fio_normalized in t_norm or t_norm in fio_normalized

        docs = await db.schedules.find().to_list(None)

        # Преподаватель ищется по всем версиям: «не найден» — это не про дату,
        # иначе опечатка в ФИО выглядела бы как «расписание не опубликовано».
        teacher_found_anywhere = any(
            match_teacher(info.get("teacher", ""))
            for doc in docs
            for _, _, info in iter_schedule_lessons(doc.get("schedule") or {})
        )
        if not teacher_found_anywhere:
            raise HTTPException(
                status_code=404, detail=f"Преподаватель '{fio}' не найден в расписании"
            )

        by_group: dict[str, list] = {}
        for doc in docs:
            by_group.setdefault(doc.get("group_name"), []).append(doc)

        week_start = day_date - timedelta(days=day_date.weekday())
        first_shift: dict[str, dict] = {}
        second_shift: dict[str, dict] = {}
        published = False

        # Устойчивый порядок обхода: иначе «основной» группой в параллели
        # становилась случайная (порядок документов в Mongo не гарантирован).
        for group_name in sorted(
            by_group, key=lambda name: group_sort_key({"group_name": name or ""})
        ):
            group_docs = by_group[group_name]

            # Версии, действующие на дату. Пусто — расписание группы на эту
            # дату ещё не выложено, группа в ответ не попадает.
            versions = applicable_versions(group_docs, day_date)
            if not versions:
                continue
            published = True

            base_shift = coerce_shift(
                (versions[0].get("shift_info") or {}).get("shift")
            )
            if base_shift is None:
                base_shift = 1

            def shift_on(group_day: date) -> int | None:
                """Смена группы на дату: по приказу на период, иначе обычная."""
                found = coerce_shift(
                    distribution_on_date(group_docs, group_day)["shift"]
                )
                return base_shift if found is None else found

            shift_today = shift_on(day_date)
            if shift_today not in (1, 2):
                continue  # 0 — группа скрыта из выдачи
            shift_target = first_shift if shift_today == 1 else second_shift

            if normalized_day:
                resolved = resolve_day(versions, day or "")
                if resolved is None:
                    continue
                group_schedule = resolved[1]
            else:
                group_schedule = merge_week(versions)

            # Время пар — по сетке звонков своей смены: 2–3 ноября часть групп
            # учится во вторую смену, и время берётся из её сетки.
            group_schedule = ScheduleService.apply_week_bell_grid(
                group_schedule, week_start, shift_on
            )

            for day_name, num, info in iter_schedule_lessons(group_schedule):
                if not match_teacher(info.get("teacher", "")):
                    continue
                if normalized_day and normalize_day_name(day_name) != normalized_day:
                    continue

                # Смена и кабинет — по дате самого дня: в недельной выдаче дни
                # периода приказа живут по приказу, остальные — по обычной сетке.
                lesson_day = day_in_week(day_name, week_start)
                distribution = (
                    distribution_on_date(group_docs, lesson_day) if lesson_day else {}
                )
                classroom, classroom_note = lesson_place(info, distribution)

                shift_target.setdefault(day_name, {})
                ScheduleService._put_lesson(
                    shift_target[day_name],
                    num,
                    group_name,
                    info.get("subject", ""),
                    classroom,
                    info.get("time"),
                    classroom_note=classroom_note,
                )

        if not published:
            raise HTTPException(
                status_code=404,
                detail=f"Расписание на {fmt_date(day_date)} ещё не опубликовано",
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
            requested_date=day_date,
            schedule=TeacherShiftSchedule(
                first_shift=first_shift, second_shift=second_shift
            ),
        )
