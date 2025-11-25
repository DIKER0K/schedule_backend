from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, Dict


class ShiftInfo(BaseModel):
    shift: int = Field(1, description="Номер смены (обычно 1 или 2)")
    room: Optional[str] = Field(None, description="Кабинет, используемый группой")
    building: Optional[int] = Field(None, description="Корпус, в котором проходят занятия", example=1)

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
    shift_info: ShiftInfo = Field(default_factory=ShiftInfo)
    updated_at: Optional[datetime] = Field(
        None,
        description="Дата и время последнего обновления расписания",
        example="2025-10-25T14:11:12.814Z"
    )
