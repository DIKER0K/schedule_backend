"""Общие фикстуры: тесты не должны ходить в прод-Mongo."""

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
    import app.services.schedule_parser as parser
    import app.services.schedule_service as schedule_service
    from mongomock_motor import AsyncMongoMockClient

    database = AsyncMongoMockClient()["college_schedule_bot"]
    monkeypatch.setattr(schedule_service, "db", database)
    monkeypatch.setattr(parser, "db", database)
    monkeypatch.setattr(bell_service, "db", database)
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
