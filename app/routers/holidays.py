from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Path, Query

from app.models.holiday import Holiday, HolidayDeleted
from app.services import holidays_service

router = APIRouter()


@router.get(
    "",
    response_model=List[Holiday],
    summary="Нерабочие дни",
    description=(
        "Календарь нерабочих дней колледжа (приказ администрации). В такие даты "
        "занятий и звонков нет. Параметр `year` оставляет только этот год."
    ),
    response_description="Список нерабочих дней по возрастанию даты.",
)
async def list_holidays(
    year: Optional[int] = Query(
        None,
        ge=2020,
        le=2100,
        description="Необязательно: только этот год, например 2026.",
    ),
):
    return await holidays_service.list_holidays(year)


@router.post(
    "",
    response_model=Holiday,
    status_code=201,
    summary="Добавить нерабочий день",
    description=(
        "Отмечает дату выходным: бот скажет «выходной, звонков нет», а расписание "
        "группы на эту дату вернёт 404 вместо пар. Повторная дата — 409."
    ),
    response_description="Добавленный выходной.",
)
async def add_holiday(payload: Holiday):
    return await holidays_service.add_holiday(payload.date, payload.title)


@router.delete(
    "/{date}",
    response_model=HolidayDeleted,
    summary="Убрать нерабочий день",
    description=(
        "Снимает дату с выходных — например, если занятия перенесли и день стал "
        "учебным. Дата не отмечена выходным — 404."
    ),
    response_description="Что сделано.",
)
async def delete_holiday(day: date = Path(..., alias="date")):
    return await holidays_service.remove_holiday(day)
