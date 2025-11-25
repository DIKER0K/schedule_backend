from typing import List
from fastapi import APIRouter, Query, UploadFile, File
from app.models.schedule import Schedule
from app.models.schedule_upload import UploadResponse
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
        "Каждый элемент включает группу, смену и структуру занятий."
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
        "Можно указать параметр `day`, чтобы получить расписание только на один день."
    ),
    response_description="Расписание группы или расписание за указанный день."
)
async def get_schedule(
    group_name: str,
    day: str | None = Query(
        None,
        description="Необязательно: день недели (например, 'Понедельник', 'Вт', 'Mon')."
    )
):
    return await ScheduleService.get_schedule_by_group(group_name, day)


# 📤 Загрузка нового DOCX расписания
@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Загрузить расписание (.docx)",
    description=(
        "Принимает DOCX-файл с расписанием и сохраняет его в базу данных. "
        "Опционально можно прикрепить JSON-файл со сменами (`group_shifts.json`). "
        "Файл должен содержать для каждой группы поля `shift`, `room` и `building`." \
        "Перед загрузкой старое расписание полностью очищается."
    ),
    response_description="Информация о загруженных расписаниях и количестве групп."
)
async def upload_schedule(
    schedule_file: UploadFile = File(
        ...,
        description="DOCX файл с расписанием занятий."
    ),
    shifts_file: UploadFile | None = File(
        None,
        description="Необязательно: JSON-файл со сменами, кабинетами и корпусом."
    )
):
    return await ScheduleService.upload_schedule(schedule_file, shifts_file)


# ❌ Удаление расписания
@router.delete(
    "/{group_name}",
    summary="Удалить расписание группы",
    description="Удаляет расписание выбранной группы из базы данных.",
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
