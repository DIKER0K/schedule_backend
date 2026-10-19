from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional


class EventType(str, Enum):
    lesson = "lesson"
    break_ = "break"
    no_classes = "no_classes"


class CurrentEvent(BaseModel):
    type: EventType
    start: Optional[str] = None
    end: Optional[str] = None
    # Человекочитаемое название: "2 пара (1-й час)", "перемена", "занятия закончились"
    label: Optional[str] = None
    # Ключ пары как в расписании звонков: "2", "2.1", "0"
    lesson_number: Optional[str] = None
    # Сколько минут осталось до конца текущего события
    remaining_minutes: Optional[int] = None


class NextBellInfo(BaseModel):
    time: str
    type: EventType
    lesson_number: Optional[str] = None
    label: Optional[str] = None
    # Сколько осталось до звонка
    in_minutes: Optional[int] = None
    in_seconds: Optional[int] = None


class LessonHalf(BaseModel):
    """Половинка пары (час), если пара из двух часов."""

    start: str                        # "10:15"
    end: str                          # "11:00"
    part: int                         # 1 или 2 — который час


class LessonSlot(BaseModel):
    """Одна пара из сетки звонков дня (для кнопок «когда N пара»)."""

    number: str                       # "1", "2", "5", "0"
    start: str                        # "10:15"
    end: str                          # "11:55"
    is_special: bool = False          # спортивный час и подобные особые метки
    label: str                        # "2 пара" / "Спортивный час"
    label_inline: str                 # "2 пара" в фразах: "идёт 2 пара"
    # часы пары, если их два; между ними — пересменок (gap_minutes)
    halves: List[LessonHalf] = Field(default_factory=list)
    break_between_minutes: Optional[int] = None


class NextBellResponse(BaseModel):
    current_time: str
    current_day: str
    current_shift: int = 1
    is_school_day: bool = True
    current_event: CurrentEvent
    next_bell: Optional[NextBellInfo] = None
    # Сетка пар дня — для кнопок «когда N пара» под сообщением
    lessons: List[LessonSlot] = Field(default_factory=list)
    # Готовый текст для бота
    message: Optional[str] = None