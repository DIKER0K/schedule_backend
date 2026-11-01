from datetime import date as date_type
from typing import Optional

from pydantic import BaseModel, Field


class Holiday(BaseModel):
    """Нерабочий день: занятий и звонков нет."""

    # Имя поля — `date`, поэтому тип импортирован под другим именем:
    # одноимённое поле и аннотация Pydantic не разбирает.
    date: date_type = Field(..., description="Дата выходного (YYYY-MM-DD)")
    title: str = Field(
        ...,
        min_length=1,
        description="Название дня: «День народного единства»",
    )


class HolidayDeleted(BaseModel):
    message: str = Field(..., description="Что сделано")
    date: Optional[date_type] = Field(None, description="Дата, снятая с выходных")
