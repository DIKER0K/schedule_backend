from enum import Enum
from pydantic import BaseModel
from typing import Optional


class EventType(str, Enum):
    lesson = "lesson"
    break_ = "break"
    no_classes = "no_classes"


class CurrentEvent(BaseModel):
    type: EventType
    start: Optional[str] = None
    end: Optional[str] = None


class NextBellInfo(BaseModel):
    time: str
    type: EventType
    lesson_number: Optional[str] = None


class NextBellResponse(BaseModel):
    current_time: str
    current_day: str
    current_event: CurrentEvent
    next_bell: Optional[NextBellInfo] = None
