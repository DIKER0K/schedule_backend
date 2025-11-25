import copy
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import main
from app.models.user import User
from app.routers import users


class InMemoryCursor:
    def __init__(self, data):
        self._data = data
        self._skip = 0
        self._limit = None

    def skip(self, value: int):
        self._skip = value
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    async def to_list(self, length: int | None = None):
        subset = self._data[self._skip :]
        effective_limit = self._limit if self._limit is not None else length
        if effective_limit is not None:
            subset = subset[:effective_limit]
        return copy.deepcopy(subset)


class InMemoryCollection:
    def __init__(self, data):
        self.data = copy.deepcopy(list(data))

    async def create_index(self, *_, **__):
        return None

    def _matches(self, item: dict, query: dict) -> bool:
        return all(item.get(key) == value for key, value in query.items())

    def find(self):
        return InMemoryCursor(self.data)

    async def find_one(self, query: dict):
        for item in self.data:
            if self._matches(item, query):
                return copy.deepcopy(item)
        return None

    async def insert_one(self, document: dict):
        self.data.append(copy.deepcopy(document))
        return types.SimpleNamespace(inserted_id=document.get("user_id"))

    async def update_one(self, query: dict, update: dict):
        for index, item in enumerate(self.data):
            if self._matches(item, query):
                updated = copy.deepcopy(item)
                updated.update(update.get("$set", {}))
                self.data[index] = updated
                return types.SimpleNamespace(matched_count=1)
        return types.SimpleNamespace(matched_count=0)

    async def delete_one(self, query: dict):
        for index, item in enumerate(self.data):
            if self._matches(item, query):
                self.data.pop(index)
                return types.SimpleNamespace(deleted_count=1)
        return types.SimpleNamespace(deleted_count=0)


class InMemoryDB:
    def __init__(self, users_data):
        self.users = InMemoryCollection(users_data)
        self.schedules = InMemoryCollection([])
        self.client = types.SimpleNamespace(close=lambda: None)


def create_test_client(monkeypatch: pytest.MonkeyPatch, users_data: list[dict]):
    fake_db = InMemoryDB(users_data)
    monkeypatch.setattr(users, "db", fake_db)
    monkeypatch.setattr(main, "db", fake_db)
    return TestClient(main.app), fake_db


def sample_user(user_id: int, username: str = "student"):
    return {
        "user_id": user_id,
        "username": username,
        "role": "student",
        "group_name": "A-1",
        "teacher_fio": None,
        "schedule_enabled": False,
        "schedule_time": "08:00",
    }


def test_list_users_paginates_and_validates_schema(monkeypatch):
    users_seed = [sample_user(1), sample_user(2, "second"), sample_user(3, "third")]
    client, _ = create_test_client(monkeypatch, users_seed)

    response = client.get("/users", params={"skip": 1, "limit": 1})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    returned = User(**payload[0])
    assert returned.user_id == 2
    assert returned.username == "second"


def test_list_users_validates_query_params(monkeypatch):
    client, _ = create_test_client(monkeypatch, [])

    bad_skip = client.get("/users", params={"skip": -1})
    bad_limit = client.get("/users", params={"limit": 0})

    assert bad_skip.status_code == 422
    assert bad_limit.status_code == 422


def test_get_user_returns_user_and_validates_schema(monkeypatch):
    user = sample_user(10, "tester")
    client, _ = create_test_client(monkeypatch, [user])

    response = client.get("/users/10")

    assert response.status_code == 200
    returned = User(**response.json())
    assert returned.user_id == user["user_id"]
    assert returned.username == user["username"]


def test_get_user_not_found(monkeypatch):
    client, _ = create_test_client(monkeypatch, [])

    response = client.get("/users/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


def test_create_user_and_reject_duplicates(monkeypatch):
    existing = sample_user(1, "existing")
    client, db = create_test_client(monkeypatch, [existing])

    new_user = sample_user(2, "newbie")
    create_response = client.post("/users", json=new_user)

    assert create_response.status_code == 200
    created = User(**create_response.json())
    assert created.user_id == new_user["user_id"]
    assert len(db.users.data) == 2

    duplicate_response = client.post("/users", json=existing)

    assert duplicate_response.status_code == 400
    assert duplicate_response.json()["detail"] == "User already exists"


def test_update_user_partially(monkeypatch):
    user = sample_user(5, "partial")
    client, db = create_test_client(monkeypatch, [user])

    response = client.put("/users/5", json={"username": "updated", "group_name": "B-2"})

    assert response.status_code == 200
    updated = User(**response.json())
    assert updated.username == "updated"
    assert updated.group_name == "B-2"
    assert updated.role == "student"
    assert any(item["username"] == "updated" for item in db.users.data)


def test_update_user_not_found(monkeypatch):
    client, _ = create_test_client(monkeypatch, [])

    response = client.put("/users/404", json={"username": "missing"})

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


def test_delete_user(monkeypatch):
    user = sample_user(7, "to-delete")
    client, db = create_test_client(monkeypatch, [user])

    response = client.delete("/users/7")

    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"
    assert db.users.data == []

    second_response = client.delete("/users/7")

    assert second_response.status_code == 404
    assert second_response.json()["detail"] == "User not found"
