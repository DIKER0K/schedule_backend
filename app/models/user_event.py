from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict

class UserEvent(BaseModel):
    user_id: int
    type: str = Field(..., description="Тип события: callback_click, command, text_message и т.д.")
    ts: datetime = Field(default_factory=datetime.utcnow)
    meta: Optional[Dict[str, str]] = Field(default=None, description="Доп. данные (название кнопки, команда и т.д.)")
