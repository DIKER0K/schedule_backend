import json

from fastapi.encoders import jsonable_encoder
from openai import OpenAI
from typing import List, Dict, Any

OPENROUTER_MODEL = "arcee-ai/trinity-large-preview:free"


class AIService:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key="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",
        )

    def ask(self, user_message: str, system_message: str) -> str:

        response = self.client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            # extra_body={"reasoning": {"enabled": True}},
        )

        return response.choices[0].message.content

    async def describe_schedule(self, schedule, day: str | None):

        schedule_json = jsonable_encoder(schedule)

        prompt = f"""
    Вот расписание группы в формате JSON:

    {json.dumps(schedule_json, ensure_ascii=False, indent=2)}

    Если указан день недели — это расписание только на этот день.

    День: {day if day else "вся неделя"}

    Сформируй ответ СТРОГО в формате:

    ТЕКСТ ДО РАСПИСАНИЯ
    ---
    1. предмет преподаватель аудитория
    2. предмет преподаватель аудитория
    3. предмет преподаватель аудитория
    ---
    ТЕКСТ ПОСЛЕ РАСПИСАНИЯ (разбор расписания)

    Правила:
    - до первого "---" напиши небольшое дружелюбное вступление
    - между "---" выведи нумерованный список пар
    - после второго "---" напиши анализ расписания
    - не добавляй других разделителей
    """

        system_prompt = """
    Ты дружелюбный AI-ассистент для студентов.

    Твоя задача:
    объяснять расписание понятно и дружелюбно.

    Ответ должен быть:
    - живым
    - понятным
    - полезным для студента

    Строго соблюдай формат с разделителями "---".
    """

        return self.ask(prompt, system_prompt)

    async def describe_teacher_schedule(self, schedule, fio: str, day: str | None):

        schedule_json = jsonable_encoder(schedule)

        prompt = f"""
    Вот расписание преподавателя {fio} в формате JSON:

    {json.dumps(schedule_json, ensure_ascii=False, indent=2)}

    День: {day if day else "вся неделя"}

    Сформируй ответ СТРОГО в формате:

    ТЕКСТ ДО РАСПИСАНИЯ
    ---
    1. предмет группа аудитория
    2. предмет группа аудитория
    3. предмет группа аудитория
    ---
    ТЕКСТ ПОСЛЕ РАСПИСАНИЯ (разбор расписания)

    Правила:
    - до первого "---" напиши небольшое дружелюбное вступление
    - между "---" выведи нумерованный список занятий преподавателя
    - после второго "---" сделай небольшой анализ расписания
    - пиши живым и дружелюбным языком
    - не добавляй других разделителей
    """

        system_prompt = """
    Ты помощник преподавателя.

    Твоя задача — объяснить расписание преподавателя
    понятно, дружелюбно и аккуратно. Ты общаешься с преподавателем.

    Ответ должен:
    - легко читаться
    - быть полезным
    - помогать быстро понять загруженность дня

    Строго соблюдай формат с разделителями "---".
    """

        return self.ask(prompt, system_prompt)
