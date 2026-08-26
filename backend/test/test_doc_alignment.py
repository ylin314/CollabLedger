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


def _join_as(owner: TestClient, joiner: TestClient, pid: int, role: str) -> None:
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": role}).json()["code"]
    assert joiner.post(f"/api/invitations/{code}/accept").status_code == 200


def test_primary_owner_privilege_on_member_ops(monkeypatch, tmp_path):
    """3.2 / 3.3: 只有主 owner 或本人可以调整/移除其他 owner。"""
    _setup(monkeypatch, tmp_path, "primary-owner.db")
    primary, second, third = _client(), _client(), _client()
    _account(primary, "Primary", "primary-po@example.com")
    second_user = _account(second, "Second", "second-po@example.com")
    third_user = _account(third, "Third", "third-po@example.com")

    pid = primary.post("/api/projects", json={"name": "多owner项目"}).json()["id"]
    _join_as(primary, second, pid, "member")
    _join_as(primary, third, pid, "member")
    assert primary.patch(f"/api/projects/{pid}/members/{second_user['id']}", json={"role": "owner"}).status_code == 200
    assert primary.patch(f"/api/projects/{pid}/members/{third_user['id']}", json={"role": "owner"}).status_code == 200

    # 普通 owner（second）不能降级另一个 owner（third）-> 403
    assert second.patch(f"/api/projects/{pid}/members/{third_user['id']}", json={"role": "member"}).status_code == 403
    # 普通 owner（second）不能移除另一个 owner（third）-> 403
    assert second.delete(f"/api/projects/{pid}/members/{third_user['id']}").status_code == 403
    # 本人可以主动退位（降级自己）
    assert second.patch(f"/api/projects/{pid}/members/{second_user['id']}", json={"role": "member"}).status_code == 200
    # 主 owner 可以降级其他 owner
    assert primary.patch(f"/api/projects/{pid}/members/{third_user['id']}", json={"role": "member"}).status_code == 200


def test_task_delete_by_creator(monkeypatch, tmp_path):
    """4.10: owner 或任务创建人可以删除任务；其他成员 403。"""
    _setup(monkeypatch, tmp_path, "task-delete.db")
    owner, creator, other = _client(), _client(), _client()
    _account(owner, "Owner", "owner-td@example.com")
    creator_user = _account(creator, "Creator", "creator-td@example.com")
    other_user = _account(other, "Other", "other-td@example.com")

    pid = owner.post("/api/projects", json={"name": "删除项目"}).json()["id"]
    _join_as(owner, creator, pid, "member")
    _join_as(owner, other, pid, "member")

    # 创建者（普通成员）创建任务后可以删除自己创建的任务
    t1 = creator.post(f"/api/projects/{pid}/tasks", json={"title": "创建者的任务"}).json()
    assert t1["created_by"] == creator_user["id"]
    # 非创建者、非 owner 的成员不能删除 -> 403
    assert other.delete(f"/api/tasks/{t1['id']}").status_code == 403
    # 创建者可以删除 -> 204
    assert creator.delete(f"/api/tasks/{t1['id']}").status_code == 204

    # owner 可以删除任意任务
    t2 = creator.post(f"/api/projects/{pid}/tasks", json={"title": "另一个任务"}).json()
    assert owner.delete(f"/api/tasks/{t2['id']}").status_code == 204


def test_review_history_has_updated_at(monkeypatch, tmp_path):
    """6.4: 评价历史项与评价对象结构一致，含 updated_at。"""
    _setup(monkeypatch, tmp_path, "review-history.db")
    owner, member = _client(), _client()
    _account(owner, "Owner", "owner-rh@example.com")
    member_user = _account(member, "Member", "member-rh@example.com")

    pid = owner.post("/api/projects", json={"name": "评价历史项目"}).json()["id"]
    _join_as(owner, member, pid, "member")
    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "任务", "assignee_id": member_user["id"]}).json()
    member.post(f"/api/tasks/{task['id']}/start", json={})
    member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 2})

    # owner 评价两次，产生两条历史
    assert owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 3}).status_code == 201
    assert owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 4.5}).status_code == 200

    history = owner.get(f"/api/tasks/{task['id']}/review/history").json()["items"]
    assert len(history) == 2
    for item in history:
        # 与 6.1 评价对象结构一致：含 updated_at 且非空
        assert "updated_at" in item and item["updated_at"]
        for field in ("id", "task_id", "reviewer_id", "reviewer_name", "quality", "comment", "created_at"):
            assert field in item


def test_invitation_response_has_is_mentor(monkeypatch, tmp_path):
    """3.4 / 3.5: 邀请对象返回 is_mentor 字段。"""
    _setup(monkeypatch, tmp_path, "invite-mentor.db")
    owner = _client()
    _account(owner, "Owner", "owner-im@example.com")
    pid = owner.post("/api/projects", json={"name": "邀请项目"}).json()["id"]

    created = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()
    assert created["is_mentor"] is False
    listed = owner.get(f"/api/projects/{pid}/invitations").json()["items"]
    assert all("is_mentor" in item for item in listed)


def test_invitation_code_is_12_uppercase_chars(monkeypatch, tmp_path):
    """3.4: 邀请码为 12 位大写字符串（含数字），不含小写或 url-safe 符号。"""
    _setup(monkeypatch, tmp_path, "invite-code-format.db")
    owner = _client()
    _account(owner, "Owner", "owner-ic@example.com")
    pid = owner.post("/api/projects", json={"name": "邀请码项目"}).json()["id"]

    # 多次生成，确认长度与字符集稳定（token_urlsafe 去符号后会偶发短于 12 位）。
    for _ in range(30):
        code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
        assert len(code) == 12, f"邀请码应为 12 位，实际 {len(code)}：{code!r}"
        assert code.isupper() or code.isdigit(), f"邀请码应全大写/数字：{code!r}"
        assert all(ch.isalnum() and (ch.isupper() or ch.isdigit()) for ch in code), f"邀请码含非法字符：{code!r}"

