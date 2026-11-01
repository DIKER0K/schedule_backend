"""Общие фикстуры: тесты не должны ходить в прод-Mongo."""

import json
import sys
from pathlib import Path

import pytest
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]


@pytest.fixture
def fake_db(monkeypatch):
    """Коллекция расписаний — mongomock вместо Motor, звонки из файлов не читаем."""
    import app.main as main_module
    import app.services.bell_service as bell_service
    import app.services.holidays_service as holidays_service
    import app.services.schedule_parser as parser
    import app.services.schedule_service as schedule_service
    from mongomock_motor import AsyncMongoMockClient

    database = AsyncMongoMockClient()["college_schedule_bot"]
    monkeypatch.setattr(schedule_service, "db", database)
    monkeypatch.setattr(parser, "db", database)
    monkeypatch.setattr(bell_service, "db", database)
    monkeypatch.setattr(holidays_service, "db", database)
    monkeypatch.setattr(main_module, "db", database)

    async def _skip_bell_files():
        return 0

    # Иначе загрузка пойдёт переписывать время пар по bell_schedule.json
    monkeypatch.setattr(
        schedule_service, "apply_bell_schedule_from_files", _skip_bell_files
    )
    return database


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Рабочая папка: загрузка пишет временный docx в текущий каталог."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def bell_files(fake_db, workdir, monkeypatch):
    """Включает настоящую подстановку времени пар из bell_schedule.json.

    `fake_db` глушит чтение файла звонков — здесь возвращаем реальную функцию
    и кладём файл в рабочую папку, чтобы проверять путь загрузки целиком.
    """
    import app.services.schedule_service as schedule_service
    from app.services.bell_service import apply_bell_schedule_from_files

    monkeypatch.setattr(
        schedule_service, "apply_bell_schedule_from_files", apply_bell_schedule_from_files
    )

    bell_data = {
        "понедельник": {"1_shift": {"1": "09:00–10:00", "2": "10:15–11:55"}},
        "вторник-четверг": {"1_shift": {"1": "09:00–10:35", "2": "10:45–12:30"}},
        "пятница": {"1_shift": {"1": "09:00–10:00", "2": "10:15–11:55"}},
    }
    (workdir / "bell_schedule.json").write_text(
        json.dumps(bell_data, ensure_ascii=False), encoding="utf-8"
    )
    return workdir


def build_docx(path, groups: dict) -> Path:
    """Собирает docx как у секретаря: заголовок группы + таблица дней.

    groups: {группа: {"title": "...", "lessons": {"1": {"Понедельник": "..."}}}}
    """
    doc = Document()
    for group, spec in groups.items():
        doc.add_paragraph(spec.get("title") or f"Расписание уроков для {group} группы")

        lessons = spec.get("lessons") or {}
        numbers = sorted(lessons, key=lambda n: int(str(n).split(".")[0]))
        table = doc.add_table(rows=1 + len(numbers), cols=1 + len(DAYS))

        for index, text in enumerate(["Пара"] + DAYS):
            table.rows[0].cells[index].text = text

        for row_index, number in enumerate(numbers, start=1):
            table.rows[row_index].cells[0].text = str(number)
            for day_index, day in enumerate(DAYS, start=1):
                table.rows[row_index].cells[day_index].text = lessons[number].get(
                    day, ""
                )

    doc.save(path)
    return Path(path)


@pytest.fixture
def make_docx(tmp_path):
    def _make(name: str, groups: dict) -> Path:
        return build_docx(tmp_path / name, groups)

    return _make
