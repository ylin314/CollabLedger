from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def test_stage2_recommendation_load_risks_and_weekly(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "stage2.db")
    api.init_db()
    owner, backend_dev, frontend_dev = _client(), _client(), _client()
    _account(owner, "组长", "owner-s2@example.com")
    backend_user = _account(backend_dev, "后端同学", "backend-s2@example.com")
    frontend_user = _account(frontend_dev, "前端同学", "frontend-s2@example.com")
    pid = owner.post("/api/projects", json={"name": "阶段二项目"}).json()["id"]
    backend_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    frontend_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert backend_dev.post(f"/api/invitations/{backend_code}/accept").status_code == 200
    assert frontend_dev.post(f"/api/invitations/{frontend_code}/accept").status_code == 200
    assert backend_dev.patch("/api/users/me", json={"skills": ["后端", "Python"], "max_concurrent_tasks": 3}).status_code == 200
    assert frontend_dev.patch("/api/users/me", json={"skills": ["前端"], "max_concurrent_tasks": 1}).status_code == 200

    busy = owner.post(f"/api/projects/{pid}/tasks", json={"title": "占满前端容量", "assignee_id": frontend_user["id"], "task_type": "前端"}).json()
    owner.post(f"/api/projects/{pid}/tasks", json={"title": "未分配后端接口", "task_type": "后端", "due_date": "2020-01-01"})
    done = owner.post(f"/api/projects/{pid}/tasks", json={"title": "已完成后端模块", "assignee_id": backend_user["id"], "task_type": "后端", "estimated_hours": 4}).json()
    backend_dev.post(f"/api/tasks/{done['id']}/start", json={})
    backend_dev.post(f"/api/tasks/{done['id']}/complete", json={"actual_hours": 3})
    owner.post(f"/api/tasks/{done['id']}/review", json={"quality": 5, "comment": "质量高"})

    rec = owner.get(f"/api/projects/{pid}/recommendations", params={"task_name": "新的后端任务", "task_type": "后端"}).json()
    names = [item["name"] for item in rec["recommendations"]]
    assert "前端同学" not in names
    assert rec["weights"] == {"skill": 0.4, "quality": 0.3, "efficiency": 0.2, "load": 0.1}
    assert rec["disclaimer"]
    assert rec["recommendations"][0]["reasons"]["evidence"]
    assert rec["excluded_overloaded"]

    load = owner.get(f"/api/projects/{pid}/members/load").json()
    frontend_load = next(item for item in load["members"] if item["user_id"] == frontend_user["id"])
    assert frontend_load["overloaded"] is True
    assert frontend_load["load_level"] == "high"

    risks = owner.get(f"/api/projects/{pid}/risks").json()
    types = {item["type"] for item in risks["risks"]}
    assert {"overdue_task", "unassigned_task", "high_member_load"} <= types
    assert all(item.get("rule") for item in risks["risks"])

    weekly = owner.get(f"/api/projects/{pid}/weekly-report").json()
    assert weekly["summary"]["tasks_total"] >= 3
    assert weekly["disclaimer"]
    assert weekly["source"]
