from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import backend.main as api
from backend.core.context import active_db_path
import backend.routers.tasks as tasks_router


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False)


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def _setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "d1.db")
    monkeypatch.setenv("RECOMMEND_SKILL_MODE", "rule")
    monkeypatch.setenv("RECOMMEND_USE_LLM_SKILL", "false")
    monkeypatch.setenv("RECOMMEND_USE_LLM_REASON", "false")
    monkeypatch.setenv("LLM_API_KEY", "")
    api.init_db()


def _member(owner: TestClient, member: TestClient, project_id: int, member_user: dict) -> None:
    code = owner.post(f"/api/projects/{project_id}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200


def test_profile_authorization_global_whitelist_freeze_and_delete(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client()
    member = _client()
    _account(owner, "组长", "d1-auth-owner@example.com")
    member_user = _account(member, "历史后端", "d1-auth-member@example.com")

    old = owner.post("/api/projects", json={"name": "历史项目"}).json()
    _member(owner, member, old["id"], member_user)
    old_task = owner.post(
        f"/api/projects/{old['id']}/tasks",
        json={"title": "历史后端接口", "task_type": "后端", "assignee_id": member_user["id"], "estimated_hours": 8},
    ).json()
    assert member.post(f"/api/tasks/{old_task['id']}/start", json={}).status_code == 200
    assert member.post(f"/api/tasks/{old_task['id']}/complete", json={"actual_hours": 4}).status_code == 200
    assert owner.post(f"/api/tasks/{old_task['id']}/review", json={"quality": 5}).status_code == 201

    current = owner.post("/api/projects", json={"name": "当前项目", "classroom_id": old["classroom_id"]}).json()
    _member(owner, member, current["id"], member_user)

    defaults = member.get("/api/users/me/authorizations")
    assert defaults.status_code == 200
    body = defaults.json()
    assert body["cross_project_profile"] is True
    assert body["global_enabled"] is True
    assert body["data_status"] == "retained"
    assert any(item["project_id"] == old["id"] and item["enabled"] for item in body["projects"])

    disabled = member.patch("/api/users/me/authorizations", json={"global_enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["data_status"] == "frozen"
    no_history = owner.get(
        f"/api/projects/{current['id']}/recommendations",
        params={"task_name": "新的后端接口", "task_type": "后端"},
    ).json()
    member_item = next(item for item in no_history["recommendations"] if item["user_id"] == member_user["id"])
    assert member_item["profile_source"] == "current"

    whitelisted = member.patch(
        "/api/users/me/authorizations",
        json={"project_overrides": {str(old["id"]): True}},
    )
    assert whitelisted.status_code == 200
    assert whitelisted.json()["projects"]
    with_history = owner.get(
        f"/api/projects/{current['id']}/recommendations",
        params={"task_name": "新的后端接口", "task_type": "后端"},
    ).json()
    member_item = next(item for item in with_history["recommendations"] if item["user_id"] == member_user["id"])
    assert member_item["profile_source"] == "historical"
    assert member_item["reasons"]["average_quality"] == 5.0

    deleted = member.delete("/api/users/me/profile-data")
    assert deleted.status_code == 200
    assert deleted.json()["data_status"] == "deleted"
    frozen_again = owner.get(
        f"/api/projects/{current['id']}/recommendations",
        params={"task_name": "新的后端接口", "task_type": "后端"},
    ).json()
    member_item = next(item for item in frozen_again["recommendations"] if item["user_id"] == member_user["id"])
    assert member_item["profile_source"] == "current"


def test_recommendation_decision_is_atomic_and_idempotent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client()
    member = _client()
    _account(owner, "组长", "d1-atomic-owner@example.com")
    member_user = _account(member, "后端同学", "d1-atomic-member@example.com")
    project = owner.post("/api/projects", json={"name": "原子项目"}).json()
    _member(owner, member, project["id"], member_user)
    task = owner.post(f"/api/projects/{project['id']}/tasks", json={"title": "待分配后端接口", "task_type": "后端"}).json()
    recommendation = owner.get(
        f"/api/projects/{project['id']}/recommendations", params={"task_id": task["id"]}
    ).json()

    first = owner.post(
        f"/api/projects/{project['id']}/recommendations/{recommendation['recommendation_id']}/decide",
        json={"user_id": member_user["id"], "note": "采纳"},
    )
    assert first.status_code == 200
    assert first.json()["task"]["assignee_id"] == member_user["id"]
    assert first.json()["task"]["reviewer_id"] is None
    second = owner.post(
        f"/api/projects/{project['id']}/recommendations/{recommendation['recommendation_id']}/decide",
        json={"user_id": member_user["id"], "note": "重复点击"},
    )
    assert second.status_code == 409

    conn = sqlite3.connect(active_db_path())
    rec = conn.execute("SELECT status FROM recommendations WHERE id=?", (recommendation["recommendation_id"],)).fetchone()
    events = conn.execute(
        "SELECT action FROM recommendation_events WHERE recommendation_id=? ORDER BY id",
        (recommendation["recommendation_id"],),
    ).fetchall()
    assigned = conn.execute("SELECT assignee_id FROM tasks WHERE id=?", (task["id"],)).fetchone()
    conn.close()
    assert rec[0] == "accept"
    assert [row[0] for row in events] == ["generated", "accept"]
    assert assigned[0] == member_user["id"]


def test_recommendation_rolls_back_when_assignment_fails(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client()
    member = _client()
    _account(owner, "组长", "d1-rollback-owner@example.com")
    member_user = _account(member, "后端同学", "d1-rollback-member@example.com")
    project = owner.post("/api/projects", json={"name": "回滚项目"}).json()
    _member(owner, member, project["id"], member_user)
    task = owner.post(f"/api/projects/{project['id']}/tasks", json={"title": "回滚后端接口", "task_type": "后端"}).json()
    recommendation = owner.get(
        f"/api/projects/{project['id']}/recommendations", params={"task_id": task["id"]}
    ).json()

    def explode(*_args, **_kwargs):
        raise RuntimeError("模拟指派失败")

    monkeypatch.setattr(tasks_router, "assign_task_in_connection", explode)
    response = owner.post(
        f"/api/projects/{project['id']}/recommendations/{recommendation['recommendation_id']}/decide",
        json={"user_id": member_user["id"], "note": "应回滚"},
    )
    assert response.status_code == 500
    conn = sqlite3.connect(active_db_path())
    rec = conn.execute("SELECT status FROM recommendations WHERE id=?", (recommendation["recommendation_id"],)).fetchone()
    assigned = conn.execute("SELECT assignee_id FROM tasks WHERE id=?", (task["id"],)).fetchone()
    event_count = conn.execute(
        "SELECT COUNT(*) FROM recommendation_events WHERE recommendation_id=? AND action != 'generated'",
        (recommendation["recommendation_id"],),
    ).fetchone()[0]
    conn.close()
    assert rec[0] == "generated"
    assert assigned[0] is None
    assert event_count == 0