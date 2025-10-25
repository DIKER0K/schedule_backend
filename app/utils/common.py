import re


def serialize_doc(doc):
    """Преобразует ObjectId в str для JSON-совместимости"""
    if not doc:
        return doc
    doc["_id"] = str(doc["_id"])
    return doc

def normalize_day_name(day: str) -> str:
    """Приводит день недели к стандартной форме (без регистра, синонимы)"""
    if not day:
        return ""
    day = day.strip().lower()
    mapping = {
        "пн": "понедельник", "понедельник": "понедельник", "mon": "понедельник",
        "вт": "вторник", "вторник": "вторник", "tue": "вторник",
        "ср": "среда", "среда": "среда", "wed": "среда",
        "чт": "четверг", "четверг": "четверг", "thu": "четверг",
        "пт": "пятница", "пятница": "пятница", "fri": "пятница",
        "сб": "суббота", "суббота": "суббота", "sat": "суббота",
    }
    return mapping.get(day, day)


def normalize_name(name: str) -> str:
    """Удаляет все лишние пробелы, точки и невидимые символы"""
    if not name:
        return ""
    name = name.strip().replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    # убрать все пробелы и точки, всё в нижний регистр
    return re.sub(r"[.\s]", "", name).lower()