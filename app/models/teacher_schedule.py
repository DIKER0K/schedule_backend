from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class ParallelLesson(BaseModel):
    """Занятие, идущее ОДНОВРЕМЕННО с основным.

    Преподаватель может вести одну пару сразу у нескольких групп: либо тот же
    предмет у параллельных групп (лекция на поток), либо разные предметы в
    разных кабинетах. Время у параллели то же, что у основной пары.
    """

    subject: str
    group: str
    classroom: Optional[str] = None


class TeacherLesson(BaseModel):
    subject: str
    group: str
    classroom: Optional[str]
    time: Optional[str] = None

    # Другие группы, у которых в это же время пара с этим же преподавателем.
    # Пустой список = обычная пара без параллелей.
    #
    # Поле добавлено ради обратной совместимости: структура слота
    # (`день -> номер пары -> TeacherLesson`) не менялась, старые поля на месте,
    # поэтому потребители, не знающие о `parallels`, работают как раньше.
    parallels: List[ParallelLesson] = Field(default_factory=list)


class TeacherShiftSchedule(BaseModel):
    first_shift: Dict[str, Dict[str, TeacherLesson]]
    second_shift: Dict[str, Dict[str, TeacherLesson]]


class TeacherScheduleResponse(BaseModel):
    teacher_fio: str
    filtered_by_day: Optional[str] = None
    schedule: TeacherShiftSchedule