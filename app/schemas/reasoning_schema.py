from pydantic import BaseModel


class ReasoningRequest(BaseModel):
    system_message: str = "Ты полезный ассистент. Отвечай на русском."
    user_message: str


class ReasoningResponse(BaseModel):
    answer: str
