from pydantic import BaseModel
from typing import List

class UploadResponse(BaseModel):
    message: str
    inserted_ids: List[str]
    total_groups: int
    first_shift: int
    second_shift: int
