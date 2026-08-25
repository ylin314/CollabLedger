from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    response = client.post("/api/auth/login", json={"email": email, "password": "password-123"})
    assert response.status_code == 200
    return response.json()["user"]


def test_invitations_roles_tasks_checkins_reviews_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "contract-p0.db")
    api.init_db()
    owner, member, viewer, outsider = _client(), _client(), _client(), _client()
    owner_user = _account(owner, "Owner", "owner@example.com")
    member_user = _account(member, "Member", "member@example.com")
    viewer_user = _account(viewer, "Viewer", "viewer@example.com")
    _account(outsider, "Outsider", "outsider@example.com")

    pid = owner.post("/api/projects", json={"name": "P0 项目"}).json()["id"]
    updated_project = owner.patch(f"/api/projects/{pid}", json={"description": "P0 契约测试", "end_date": "2026-12-31"})
    assert updated_project.status_code == 200 and updated_project.json()["description"] == "P0 契约测试"
    assert outsider.get(f"/api/projects/{pid}").status_code == 403

    member_invite = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member", "expires_in_hours": 48, "max_uses": 1})
    assert member_invite.status_code == 201
    code = member_invite.json()["code"]
    assert member.get(f"/api/invitations/{code}").json()["valid"] is True
    accepted = member.post(f"/api/invitations/{code}/accept")
    assert accepted.status_code == 200 and accepted.json()["role"] == "member"
    assert member.post(f"/api/invitations/{code}/accept").status_code == 409

    viewer_invite = owner.post(f"/api/projects/{pid}/invitations", json={"role": "viewer"}).json()
    assert viewer.post(f"/api/invitations/{viewer_invite['code']}/accept").status_code == 200
    assert owner.get(f"/api/projects/{pid}/invitations").json()["items"]
    revoked = owner.post(f"/api/invitations/{viewer_invite['id']}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True

    members = owner.get(f"/api/projects/{pid}/members").json()["items"]
    assert {item["role"] for item in members} == {"owner", "member", "viewer"}
    assert viewer.post(f"/api/projects/{pid}/tasks", json={"title": "viewer forbidden"}).status_code == 403

    task = owner.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "完成鉴权", "description": "补测试", "assignee_id": member_user["id"], "task_type": "后端", "priority": "high", "due_date": "2026-09-10", "estimated_hours": 8},
    )
    assert task.status_code == 201, task.text
    tid = task.json()["id"]
    filtered = owner.get(f"/api/projects/{pid}/tasks", params={"assignee_id": member_user["id"], "task_type": "后端", "keyword": "鉴权", "sort": "priority"})
    assert filtered.json()["total"] == 1

    assert member.post(f"/api/tasks/{tid}/start", json={"note": "开始"}).json()["status"] == "in_progress"
    checkin = member.post(f"/api/tasks/{tid}/checkins", json={"content": "完成登录接口", "hours": 2.5, "blockers": "无"})
    assert checkin.status_code == 201 and checkin.json()["user_id"] == member_user["id"]
    assert owner.get(f"/api/tasks/{tid}/checkins").json()["items"][0]["hours"] == 2.5
    assert owner.get(f"/api/projects/{pid}/checkins", params={"user_id": member_user["id"]}).json()["total"] == 1

    assert member.post(f"/api/tasks/{tid}/pause", json={"note": "等待联调"}).json()["status"] == "paused"
    assert member.post(f"/api/tasks/{tid}/resume", json={"note": "继续"}).json()["status"] == "in_progress"
    completed = member.post(f"/api/tasks/{tid}/complete", json={"note": "完成", "actual_hours": 7.5})
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    assert len(owner.get(f"/api/tasks/{tid}/logs").json()["items"]) >= 5

    state_task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "状态流转任务"}).json()
    state_tid = state_task["id"]
    assigned = owner.post(f"/api/tasks/{state_tid}/assign", json={"assignee_id": member_user["id"], "note": "指派"})
    assert assigned.status_code == 200 and assigned.json()["status"] == "assigned"
    edited = owner.patch(f"/api/tasks/{state_tid}", json={"priority": "low", "estimated_hours": 2, "actual_hours": 1})
    assert edited.status_code == 200 and edited.json()["priority"] == "low"
    assert owner.get(f"/api/tasks/{state_tid}").status_code == 200
    assert owner.post(f"/api/tasks/{state_tid}/overdue", json={"note": "延期"}).json()["status"] == "overdue"
    assert owner.post(f"/api/tasks/{state_tid}/unfinished", json={"note": "项目结束"}).json()["status"] == "unfinished"
    assert owner.delete(f"/api/tasks/{state_tid}").status_code == 204
    assert owner.get(f"/api/tasks/{state_tid}").status_code == 404

    first_review = owner.post(f"/api/tasks/{tid}/review", json={"quality": 4.5, "comment": "质量好"})
    assert first_review.status_code == 201 and first_review.json()["quality"] == 4.5
    second_review = owner.post(f"/api/tasks/{tid}/review", json={"quality": 4.8, "comment": "补充评价"})
    assert second_review.status_code == 200
    assert len(owner.get(f"/api/tasks/{tid}/review/history").json()["items"]) == 2

    role_change = owner.patch(f"/api/projects/{pid}/members/{viewer_user['id']}", json={"role": "member"})
    assert role_change.status_code == 200
    assert owner.delete(f"/api/projects/{pid}/members/{owner_user['id']}").status_code == 409
    assert owner.delete(f"/api/projects/{pid}/members/{viewer_user['id']}").status_code == 204
    assert viewer.get(f"/api/projects/{pid}").status_code == 403

    throwaway = owner.post("/api/projects", json={"name": "待删除项目"}).json()["id"]
    assert owner.delete(f"/api/projects/{throwaway}").status_code == 204
    assert owner.get(f"/api/projects/{throwaway}").status_code == 404
