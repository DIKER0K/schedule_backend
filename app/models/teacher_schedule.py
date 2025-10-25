from pydantic import BaseModel
from typing import Dict, Optional

class TeacherLesson(BaseModel):
    subject: str
    group: str
    classroom: Optional[str]

class TeacherShiftSchedule(BaseModel):
    first_shift: Dict[str, Dict[str, TeacherLesson]]
    second_shift: Dict[str, Dict[str, TeacherLesson]]

class TeacherScheduleResponse(BaseModel):
    teacher_fio: str
    filtered_by_day: Optional[str] = None
    schedule: TeacherShiftSchedule
