from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def _setup(monkeypatch, tmp_path, filename: str):
    db_path = tmp_path / filename
    monkeypatch.setattr(api, "DB_PATH", db_path)
    api.init_db()
    return db_path


def test_reviewer_assignment_and_review_permission(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "reviewer.db")
    owner, member, reviewer = _client(), _client(), _client()
    _account(owner, "Owner", "owner-rv@example.com")
    member_user = _account(member, "Member", "member-rv@example.com")
    reviewer_user = _account(reviewer, "Reviewer", "reviewer-rv@example.com")

    pid = owner.post("/api/projects", json={"name": "评审项目"}).json()["id"]
    member_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{member_code}/accept").status_code == 200
    # 导师以 viewer 身份加入，可被指定为评审人
    mentor_code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "viewer", "is_mentor": True}).json()["code"]
    assert reviewer.post(f"/api/invitations/{mentor_code}/accept").status_code == 200

    task = owner.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "后端任务", "assignee_id": member_user["id"], "reviewer_id": reviewer_user["id"]},
    ).json()
    assert task["reviewer_id"] == reviewer_user["id"] and task["reviewer_name"] == "Reviewer"

    # 未完成任务不能评价
    assert reviewer.post(f"/api/tasks/{task['id']}/review", json={"quality": 4}).status_code == 409
    member.post(f"/api/tasks/{task['id']}/start", json={})
    member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 3})

    # 负责人（非评审人）不能评价
    assert member.post(f"/api/tasks/{task['id']}/review", json={"quality": 4}).status_code == 403
    # 指定的评审人（viewer 导师）可以评价
    review = reviewer.post(f"/api/tasks/{task['id']}/review", json={"quality": 4.5, "comment": "通过"})
    assert review.status_code == 201 and review.json()["quality"] == 4.5
    assert owner.get(f"/api/tasks/{task['id']}").json()["quality"] == 4.5


def test_reviewer_id_must_be_member_and_update_permission(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "reviewer-perm.db")
    owner, member, outsider = _client(), _client(), _client()
    _account(owner, "Owner", "owner-rp@example.com")
    member_user = _account(member, "Member", "member-rp@example.com")
    outsider_user = _account(outsider, "Outsider", "outsider-rp@example.com")

    pid = owner.post("/api/projects", json={"name": "权限项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200

    # 评审人必须是项目成员
    bad = owner.post(f"/api/projects/{pid}/tasks", json={"title": "T", "reviewer_id": outsider_user["id"]})
    assert bad.status_code == 400

    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "T", "assignee_id": member_user["id"]}).json()
    assert task["reviewer_id"] is None
    # 普通成员（即使是负责人）不能改评审人
    assert member.patch(f"/api/tasks/{task['id']}", json={"reviewer_id": member_user["id"]}).status_code == 403
    # owner 可以设置评审人，也可以清空
    assert owner.patch(f"/api/tasks/{task['id']}", json={"reviewer_id": member_user["id"]}).json()["reviewer_id"] == member_user["id"]
    assert owner.patch(f"/api/tasks/{task['id']}", json={"reviewer_id": None}).json()["reviewer_id"] is None


def test_create_project_with_mentors(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "mentors.db")
    owner, mentor = _client(), _client()
    _account(owner, "Owner", "owner-mt@example.com")
    _account(mentor, "Mentor", "mentor-mt@example.com")

    created = owner.post("/api/projects", json={"name": "带导师项目", "mentors": [{"email": "mentor-mt@example.com"}]})
    body = created.json()
    assert created.status_code == 201
    assert len(body["mentor_invitations"]) == 1
    inv = body["mentor_invitations"][0]
    assert inv["role"] == "viewer" and inv["is_mentor"] is True and inv["max_uses"] == 1
    assert inv["invite_url"].endswith(inv["code"])

    # 导师接受邀请后以 viewer 身份加入
    joined = mentor.post(f"/api/invitations/{inv['code']}/accept")
    assert joined.status_code == 200 and joined.json()["role"] == "viewer"

    # 没有 mentors 时不返回 mentor_invitations
    plain = owner.post("/api/projects", json={"name": "普通项目"}).json()
    assert "mentor_invitations" not in plain
