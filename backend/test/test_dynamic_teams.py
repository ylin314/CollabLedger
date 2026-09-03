from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as api


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


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



def test_member_cannot_change_task_participants_but_owner_can(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "participant-permission.db")
    api.init_db()
    owner, assignee, actor, added = (TestClient(api.app, base_url="https://testserver") for _ in range(4))
    owner_user = _account(owner, "Owner", "participant-owner@example.com")
    assignee_user = _account(assignee, "Assignee", "participant-assignee@example.com")
    actor_user = _account(actor, "Actor", "participant-actor@example.com")
    added_user = _account(added, "Added", "participant-added@example.com")
    project_id = owner.post("/api/projects", json={"name": "参与者权限项目"}).json()["id"]
    for client in (assignee, actor, added):
        code = owner.post(f"/api/projects/{project_id}/invitations", json={"role": "member"}).json()["code"]
        assert client.post(f"/api/invitations/{code}/accept").status_code == 200
    task = owner.post(
        f"/api/projects/{project_id}/tasks",
        json={"title": "受保护任务", "assignee_id": assignee_user["id"]},
    ).json()

    changed_by_member = actor.patch(f"/api/tasks/{task['id']}", json={"participant_ids": [actor_user["id"], added_user["id"]]})
    assert changed_by_member.status_code == 403
    assert owner.get(f"/api/tasks/{task['id']}").json()["participant_ids"] == [assignee_user["id"]]

    changed_by_owner = owner.patch(f"/api/tasks/{task['id']}", json={"participant_ids": [added_user["id"]]})
    assert changed_by_owner.status_code == 200
    assert set(changed_by_owner.json()["participant_ids"]) == {assignee_user["id"], added_user["id"]}
    logs = owner.get(f"/api/tasks/{task['id']}/logs").json()["items"]
    assert any(log["action"] == "participants_updated" and "更新参与者" in (log["note"] or "") for log in logs)
    conn = api.db()
    audit = conn.execute(
        "SELECT actor_id,status_code,request_path FROM audit_logs "
        "WHERE request_path=? ORDER BY id DESC LIMIT 1",
        (f"/api/tasks/{task['id']}",),
    ).fetchone()
    conn.close()
    assert audit["actor_id"] == owner_user["id"] and audit["status_code"] == 200 and audit["request_path"] == f"/api/tasks/{task['id']}"
