from datetime import datetime
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
