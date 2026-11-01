from datetime import date
from pydantic import BaseModel
from typing import Dict, List, Optional

class UploadResponse(BaseModel):
    message: str
    inserted_ids: List[str]
    total_groups: int
    first_shift: int
    second_shift: int
    # Версия, которая записана этой загрузкой
    valid_from: Optional[date] = None
    # Сколько версий расписания стало в базе всего
    total_versions: int = 0
    # Заменены ли прежние версии групп из файла
    replace_all: bool = False
    # Дата начала по каждой группе (YYYY-MM-DD): в одном файле они могут различаться
    dates: Dict[str, str] = {}
