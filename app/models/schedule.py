from datetime import date, datetime
from pydantic import BaseModel, Field
from typing import Optional, Dict

class Lesson(BaseModel):
    subject: Optional[str] = None
    teacher: Optional[str] = None
    classroom: Optional[str] = None
    time: Optional[str] = None

class ScheduleData(BaseModel):
    zero_lesson: Dict[str, Lesson] = {}
    days: Dict[str, Dict[str, Lesson]] = {}

class Schedule(BaseModel):
    group_name: str = Field(..., description="Название группы")
    schedule: ScheduleData
    shift_info: dict = Field(default_factory=dict)
    # Смена, кабинет и корпус на период (приказ администрации). Постоянная
    # смена группы остаётся в shift_info, здесь — действующая на дату.
    shift: Optional[int] = Field(
        None,
        description="Смена, действующая на запрошенную дату (1 или 2)",
    )
    classroom: Optional[str] = Field(
        None,
        description="Кабинет группы на период; пусто — не закреплён",
    )
    building: Optional[int] = Field(
        None,
        description="Корпус на период (например, 2)",
    )
    classroom_note: Optional[str] = Field(
        None,
        description="Пометка вместо номера кабинета: «ПП» (практика) или «ДО» (дистанционно)",
    )
    updated_at: Optional[datetime] = Field(
        None,
        description="Дата и время последнего обновления расписания",
        example="2025-10-25T14:11:12.814Z"
    )
    # Объявление дня из разового правила (например, «все учатся во 2 корпусе»)
    note: Optional[str] = Field(
        None,
        description="Объявление дня, если на сегодня есть приказ администрации",
    )
    # Версия расписания, из которой взяты занятия
    valid_from: Optional[date] = Field(
        None,
        description="С какой даты действует версия расписания, из которой взят ответ",
    )
    valid_to: Optional[date] = Field(
        None,
        description="По какую дату включительно действует версия; пусто — до следующей",
    )
    requested_date: Optional[date] = Field(
        None,
        description="Дата, на которую запрошено расписание",
    )
    day: Optional[str] = Field(
        None,
        description="День недели, если запрос был за один день",
    )
