from pydantic import BaseModel, Field
from typing import Optional

class User(BaseModel):
    user_id: int = Field(..., unique=True)
    username: Optional[str] = None
    role: str = Field(default="student")
    group_name: Optional[str] = None
    teacher_fio: Optional[str] = None
    schedule_enabled: bool = False
    schedule_time: str = "08:00"
