from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import backend.main as api
from backend.core.context import active_db_path


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False)


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def _setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "d6.db")
    monkeypatch.setenv("RECOMMEND_SKILL_MODE", "rule")
    monkeypatch.setenv("RECOMMEND_USE_LLM_SKILL", "false")
    monkeypatch.setenv("RECOMMEND_USE_LLM_REASON", "false")
    api.init_db()


def _join(owner: TestClient, member: TestClient, project_id: int) -> None:
    code = owner.post(f"/api/projects/{project_id}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200


def test_profile_contract_confirmed_scope_and_cold_start(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); member = _client()
    owner_user = _account(owner, "组长", "d6-contract-owner@example.com")
    member_user = _account(member, "后端同学", "d6-contract-member@example.com")
    assert member.patch("/api/users/me", json={"skills": ["后端", "Python"]}).status_code == 200
    project = owner.post("/api/projects", json={"name": "画像契约项目"}).json()
    _join(owner, member, project["id"])
    task = owner.post(
        f"/api/projects/{project['id']}/tasks",
        json={
            "title": "实现后端接口 API",
            "task_type": "后端",
            "assignee_id": member_user["id"],
            "participant_ids": [owner_user["id"]],
            "estimated_hours": 8,
            "due_date": "2099-01-01",
        },
    ).json()
    assert member.post(f"/api/tasks/{task['id']}/start", json={}).status_code == 200
    assert member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 6}).status_code == 200
    assert owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 4.5}).status_code == 201
    pending = member.post(
        f"/api/projects/{project['id']}/contributions",
        json={"kind": "code", "title": "待确认代码贡献"},
    ).json()
    confirmed = member.post(
        f"/api/projects/{project['id']}/contributions",
        json={"kind": "code", "title": "已确认代码贡献"},
    ).json()
    assert owner.post(f"/api/contributions/{confirmed['id']}/confirm", json={}).status_code == 200

    profile = member.get("/api/users/me/profile")
    assert profile.status_code == 200
    body = profile.json()
    assert body["project_count"] == body["projects_count"] == 1
    assert body["completed_task_count"] == 1
    assert body["efficiency"] == body["average_efficiency"] == 0.75
    assert body["on_time_rate"] == 1.0
    assert body["contributions_total"] == 1
    assert body["collaboration_types"] == [{"type": "code", "count": 1, "ratio": 1.0}]
    assert body["top_skills"] and body["top_skills"][0]["cold_start"] is False
    sources = {item["source"]: item["count"] for item in body["data_sources"]}
    assert sources["confirmed_contributions"] == 1
    assert sources["self_declared_skills"] == 2
    assert body["source_projects"][0]["project_id"] == project["id"]
    assert "pending/disputed" in body["calculation_notes"]["contributions"]

    conn = sqlite3.connect(active_db_path())
    conn.row_factory = sqlite3.Row
    pending_month = conn.execute("SELECT substr(occurred_at,1,7) month FROM contributions WHERE id=?", (pending["id"],)).fetchone()["month"]
    conn.execute("UPDATE contributions SET occurred_at='1999-01-01T00:00:00Z' WHERE id=?", (pending["id"],))
    conn.commit(); conn.close()
    refreshed = member.get("/api/users/me/profile").json()
    assert refreshed["contributions_total"] == 1
    assert pending_month != "1999-01"
    assert refreshed["active_months"] == body["active_months"]


def test_collaborations_require_bilateral_project_authorization(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); member = _client()
    owner_user = _account(owner, "组长", "d6-collab-owner@example.com")
    member_user = _account(member, "协作者", "d6-collab-member@example.com")
    project = owner.post("/api/projects", json={"name": "共同项目"}).json()
    _join(owner, member, project["id"])
    task = owner.post(
        f"/api/projects/{project['id']}/tasks",
        json={
            "title": "共同后端接口",
            "assignee_id": member_user["id"],
            "participant_ids": [owner_user["id"]],
            "task_type": "后端",
        },
    )
    assert task.status_code == 201

    body = owner.get("/api/users/me/collaborations").json()
    node = next(item for item in body["items"] if item["user_id"] == member_user["id"])
    assert node["shared_project_count"] == 1
    assert node["shared_task_count"] == 1
    assert node["cooperation_score"] == 35
    assert node["calculation"]["personality_inference"] is False

    assert member.patch(
        "/api/users/me/authorizations",
        json={"project_overrides": {str(project["id"]): False}},
    ).status_code == 200
    assert owner.get("/api/users/me/collaborations").json()["items"] == []

    assert member.patch(
        "/api/users/me/authorizations",
        json={"project_overrides": {str(project["id"]): True}},
    ).status_code == 200
    assert owner.get("/api/users/me/collaborations").json()["items"]


def test_long_term_recommendations_honest_history_freeze_and_declared_cold_start(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); member = _client(); newcomer = _client()
    _account(owner, "组长", "d6-rec-owner@example.com")
    member_user = _account(member, "历史后端", "d6-rec-member@example.com")
    _account(newcomer, "新同学", "d6-rec-new@example.com")
    project = owner.post("/api/projects", json={"name": "推荐历史项目"}).json()
    _join(owner, member, project["id"])
    task = owner.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "后端接口 API", "task_type": "后端", "assignee_id": member_user["id"], "estimated_hours": 4},
    ).json()
    member.post(f"/api/tasks/{task['id']}/start", json={})
    member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 3})
    owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 5})

    historical = member.get("/api/users/me/recommendations").json()
    assert historical["recommendations"]
    assert historical["recommendations"][0]["sample_count"] == 1
    assert historical["recommendations"][0]["cold_start"] is False
    assert historical["recommendations"][0]["source_project_ids"] == [project["id"]]

    frozen = member.patch("/api/users/me/authorizations", json={"global_enabled": False})
    assert frozen.status_code == 200 and frozen.json()["data_status"] == "frozen"
    stopped = member.get("/api/users/me/recommendations").json()
    assert stopped["recommendations"] == []
    assert stopped["data_status"] == "frozen"

    assert newcomer.patch("/api/users/me", json={"skills": ["文档", "答辩"]}).status_code == 200
    cold = newcomer.get("/api/users/me/recommendations").json()
    assert cold["recommendations"]
    assert all(item["cold_start"] and item["sample_count"] == 0 and item["score"] == 50 for item in cold["recommendations"])
    assert all(item["data_sources"] == ["self_declared_skills"] for item in cold["recommendations"])


def test_leaving_shared_project_revokes_other_profile_access(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); member = _client()
    _account(owner, "组长", "d6-left-owner@example.com")
    member_user = _account(member, "离组成员", "d6-left-member@example.com")
    project = owner.post("/api/projects", json={"name": "退出项目"}).json()
    _join(owner, member, project["id"])
    assert owner.get(f"/api/users/{member_user['id']}/profile").status_code == 200
    assert owner.delete(f"/api/projects/{project['id']}/members/{member_user['id']}").status_code == 204
    assert owner.get(f"/api/users/{member_user['id']}/profile").status_code == 403


def test_history_contract_filters_and_respects_profile_authorization(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    owner = _client(); member = _client()
    _account(owner, "履历访问组长", "d6-history-owner@example.com")
    member_user = _account(member, "履历访问成员", "d6-history-member@example.com")
    classroom = owner.post("/api/classrooms", json={"name": "履历访问班级"}).json()
    assert owner.post(f"/api/classrooms/{classroom['id']}/members", json={"user_id": member_user["id"], "role": "student"}).status_code == 201

    shared = owner.post(
        "/api/projects",
        json={"name": "共享课程项目", "project_type": "课程项目", "start_date": "2026-01-01", "end_date": "2026-06-30"},
    ).json()
    _join(owner, member, shared["id"])
    shared_task = owner.post(
        f"/api/projects/{shared['id']}/tasks",
        json={"title": "共享履历任务", "assignee_id": member_user["id"]},
    ).json()
    assert member.post(f"/api/tasks/{shared_task['id']}/start", json={}).status_code == 200
    assert member.post(f"/api/tasks/{shared_task['id']}/complete", json={"actual_hours": 2}).status_code == 200
    assert owner.post(f"/api/tasks/{shared_task['id']}/review", json={"quality": 4.0}).status_code == 201

    private = member.post(
        "/api/projects",
        json={"name": "私人研究项目", "project_type": "研究项目", "start_date": "2025-01-01", "end_date": "2025-12-31"},
    ).json()
    private_task = member.post(
        f"/api/projects/{private['id']}/tasks",
        json={"title": "私人履历任务", "assignee_id": member_user["id"]},
    ).json()
    assert member.post(f"/api/tasks/{private_task['id']}/start", json={}).status_code == 200
    assert member.post(f"/api/tasks/{private_task['id']}/complete", json={"actual_hours": 1}).status_code == 200

    assert member.patch(
        "/api/users/me/authorizations",
        json={"global_enabled": False, "project_overrides": {str(shared["id"]): True}},
    ).status_code == 200

    visible = owner.get(f"/api/users/profile/{member_user['id']}/history")
    assert visible.status_code == 200, visible.text
    body = visible.json()
    assert body["total"] == 1
    assert body["page"] == 1 and body["page_size"] == 20
    assert body["items"][0]["project_id"] == shared["id"]
    assert body["items"][0]["tasks_completed"] == 1
    assert body["items"][0]["average_quality"] == 4.0
    assert all(row["project_id"] == shared["id"] for row in body["tasks"])
    assert private["id"] not in {row["project_id"] for row in body["projects"]}

    own = member.get("/api/users/me/history", params={"project_type": "课程项目", "year": 2026})
    assert own.status_code == 200, own.text
    own_body = own.json()
    assert own_body["total"] == 1
    assert own_body["items"][0]["project_id"] == shared["id"]
