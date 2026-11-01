from datetime import date
from typing import List
from fastapi import APIRouter, Form, Query, UploadFile, File
from app.models.schedule import Schedule
from app.models.schedule_upload import ClassroomUploadResponse, UploadResponse
from app.models.teacher_schedule import TeacherScheduleResponse
from app.services.schedule_service import ScheduleService

router = APIRouter()

# 📘 Получение всех расписаний
@router.get(
    "/",
    response_model=List[Schedule],
    summary="Получить все расписания",
    description=(
        "Возвращает список всех расписаний, загруженных в базу данных. "
        "Каждый элемент включает группу, смену и структуру занятий. "
        "На группу отдаётся одна запись — версия расписания, действующая сегодня."
    ),
    response_description="Список всех групп с их расписанием."
)
async def get_all_schedules():
    return await ScheduleService.get_all_schedules()


# 📅 Расписание конкретной группы
@router.get(
    "/{group_name}",
    response_model=Schedule,
    summary="Получить расписание группы",
    description=(
        "Возвращает полное расписание группы. "
        "Можно указать параметр `day`, чтобы получить расписание только на один день. "
        "Параметр `date` (YYYY-MM-DD) выбирает версию расписания, действующую на эту "
        "дату; по умолчанию — сегодня. Если в выбранной версии дня нет, день берётся "
        "из предыдущей версии."
    ),
    response_description="Расписание группы или расписание за указанный день."
)
async def get_schedule(
    group_name: str,
    day: str | None = Query(
        None,
        description="Необязательно: день недели (например, 'Понедельник', 'Вт', 'Mon')."
    ),
    date_param: date | None = Query(
        None,
        alias="date",
        description=(
            "Необязательно: дата, на которую нужно расписание (YYYY-MM-DD). "
            "По умолчанию — сегодня по Asia/Yekaterinburg."
        ),
    ),
):
    return await ScheduleService.get_schedule_by_group(group_name, day, date_param)


# 📤 Загрузка нового DOCX расписания
@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Загрузить расписание (.docx)",
    description=(
        "Принимает DOCX-файл с расписанием и записывает его как версию расписания. "
        "Опционально можно прикрепить JSON-файл со сменами (`group_shifts.json`). "
        "Дата начала версии берётся из заголовка файла («... группы с 02.11.2026 г.»); "
        "для групп без даты в заголовке — из параметра `valid_from`. "
        "Прежние версии НЕ удаляются: повторная загрузка файла с той же датой "
        "заменяет только эту версию. `replace_all=true` сносит прежние версии "
        "групп, которые есть в файле (для случая, когда файл действительно полный)."
    ),
    response_description="Сколько групп записано, с какой даты и сколько версий в базе."
)
async def upload_schedule(
    schedule_file: UploadFile = File(
        ...,
        description="DOCX файл с расписанием занятий."
    ),
    shifts_file: UploadFile | None = File(
        None,
        description="Необязательно: JSON-файл со сменами и кабинетами."
    ),
    valid_from: str | None = Query(
        None,
        description=(
            "Необязательно: дата начала версии (2026-11-02 или 02.11.2026), "
            "если её нет в заголовке файла."
        ),
    ),
    replace_all: bool = Query(
        False,
        description="Снести прежние версии групп из файла (по умолчанию — нет).",
    ),
    valid_from_form: str | None = Form(
        None,
        alias="valid_from",
        description="То же, что valid_from в строке запроса, но полем формы.",
    ),
    replace_all_form: bool = Form(
        False,
        alias="replace_all",
        description="То же, что replace_all в строке запроса, но полем формы.",
    ),
):
    return await ScheduleService.upload_schedule(
        schedule_file,
        shifts_file,
        valid_from=valid_from_form or valid_from,
        replace_all=replace_all_form or replace_all,
    )


# 🏫 Распределение групп по кабинетам на период
@router.post(
    "/classrooms/upload",
    response_model=ClassroomUploadResponse,
    summary="Загрузить распределение групп по кабинетам на период",
    description=(
        "Принимает DOCX «Распределение групп по кабинетам на ...»: таблица "
        "«Группа | Кабинет», разбитая блоками «1 СМЕНА» / «2 СМЕНА». Смена и "
        "кабинет записываются в ВЕРСИИ расписания, действующие на период, — "
        "`group_shifts.json` и обычная смена группы не меняются. Период "
        "берётся из заголовка файла («на 2 и 3 ноября 2026»); `valid_from` / "
        "`valid_to` нужны, только если дат в заголовке нет. Группа, которой нет "
        "в файле, сохраняет прежние значения. Если расписания на период ещё нет "
        "— ответ 409: пустые версии не создаются. Второй ответ 409 — если на "
        "версию уже загружено распределение на другой период: приказ на новую "
        "неделю грузится после расписания на эту неделю."
    ),
    response_description=(
        "Сколько групп обновлено, распределение по сменам и группы без кабинета."
    ),
)
async def upload_classrooms(
    file: UploadFile = File(
        ...,
        description="DOCX с распределением групп по кабинетам.",
    ),
    valid_from: str | None = Query(
        None,
        description="Первый день периода (2026-11-02 или 02.11.2026), если его нет в заголовке файла.",
    ),
    valid_to: str | None = Query(
        None,
        description="Последний день периода, если его нет в заголовке файла.",
    ),
    building: int | None = Query(
        None,
        description="Корпус на период (в приказе на 2–3 ноября 2026 — 2).",
    ),
    valid_from_form: str | None = Form(
        None,
        alias="valid_from",
        description="То же, что valid_from в строке запроса, но полем формы.",
    ),
    valid_to_form: str | None = Form(
        None,
        alias="valid_to",
        description="То же, что valid_to в строке запроса, но полем формы.",
    ),
    building_form: int | None = Form(
        None,
        alias="building",
        description="То же, что building в строке запроса, но полем формы.",
    ),
):
    return await ScheduleService.upload_classrooms(
        file,
        valid_from=valid_from_form or valid_from,
        valid_to=valid_to_form or valid_to,
        building=building_form if building_form is not None else building,
    )


# ❌ Удаление расписания
@router.delete(
    "/{group_name}",
    summary="Удалить расписание группы",
    description="Удаляет расписание выбранной группы из базы данных (все версии).",
    response_description="Сообщение об успешном удалении."
)
async def delete_schedule(group_name: str):
    return await ScheduleService.delete_schedule(group_name)


# 👨‍🏫 Расписание преподавателя
@router.get(
    "/teacher/{fio:path}",
    response_model=TeacherScheduleResponse,
    summary="Получить расписание преподавателя",
    description=(
        "Возвращает расписание для указанного преподавателя. "
        "Можно указать параметр `day`, чтобы получить только занятия на выбранный день."
    ),
    response_description="Расписание преподавателя по дням и сменам."
)
async def get_teacher_schedule(
    fio: str,
    day: str | None = Query(
        None,
        description="Необязательно: день недели (например, 'Понедельник', 'Вт', 'Mon')."
    )
):
    return await ScheduleService.get_teacher_schedule(fio, day)
