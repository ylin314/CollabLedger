from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as api


def test_classroom_project_roster_and_task_participants(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "dynamic.db")
    api.init_db()
    owner = TestClient(api.app, base_url="https://testserver")
    first = owner.post("/api/auth/register", json={"name": "甲", "email": "a@dynamic.example", "password": "password-123"}).json()
    owner.post("/api/auth/login", json={"email": "a@dynamic.example", "password": "password-123"})
    other = TestClient(api.app, base_url="https://testserver")
    second = other.post("/api/auth/register", json={"name": "乙", "email": "b@dynamic.example", "password": "password-123"}).json()
    other.post("/api/auth/login", json={"email": "b@dynamic.example", "password": "password-123"})
    classroom = owner.post("/api/classrooms", json={"name": "动态班"}).json()
    assert owner.post(f"/api/classrooms/{classroom['id']}/members", json={"user_id": second["id"]}).status_code == 201
    project = owner.post("/api/projects", json={"name": "项目甲", "classroom_id": classroom["id"], "member_ids": [second["id"]]}).json()
    task = owner.post(f"/api/projects/{project['id']}/tasks", json={"title": "联合任务", "assignee_id": first["id"], "participant_ids": [second["id"]]}).json()
    assert set(task["participant_ids"]) == {first["id"], second["id"]}
    assert owner.delete(f"/api/projects/{project['id']}/members/{second['id']}").status_code == 204
    assert [item["user_id"] for item in owner.get(f"/api/projects/{project['id']}/members").json()["items"]] == [first["id"]]
