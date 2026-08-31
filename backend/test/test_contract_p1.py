from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

import backend.main as api
from backend.agent import AgentConfig, AgentRuntime


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def test_contributions_analytics_exports_and_agent(tmp_path, monkeypatch):
    db_path = tmp_path / "contract-p1.db"
    monkeypatch.setattr(api, "DB_PATH", db_path)
    api.init_db()
    monkeypatch.setattr(api, "get_agent_runtime", lambda: AgentRuntime(db_path, AgentConfig(base_url="", api_key="", model="test")))

    owner, member = _client(), _client()
    _account(owner, "Owner", "owner-p1@example.com")
    member_user = _account(member, "Member", "member-p1@example.com")
    pid = owner.post("/api/projects", json={"name": "P1 项目"}).json()["id"]
    code = owner.post(f"/api/projects/{pid}/invitations", json={"role": "member"}).json()["code"]
    assert member.post(f"/api/invitations/{code}/accept").status_code == 200

    task = owner.post(f"/api/projects/{pid}/tasks", json={"title": "后端实现", "assignee_id": member_user["id"], "task_type": "后端", "estimated_hours": 5}).json()
    member.post(f"/api/tasks/{task['id']}/start", json={})
    member.post(f"/api/tasks/{task['id']}/complete", json={"actual_hours": 4})
    owner.post(f"/api/tasks/{task['id']}/review", json={"quality": 4.5, "comment": "通过"})

    contribution = member.post(
        f"/api/projects/{pid}/contributions",
        json={"user_id": member_user["id"], "kind": "code", "title": "完成后端", "description": "实现接口", "quantity": 2, "evidence_url": "https://example.com/pr/1"},
    )
    assert contribution.status_code == 201 and contribution.json()["status"] == "pending"
    cid = contribution.json()["id"]
    assert owner.get(f"/api/contributions/{cid}").json()["title"] == "完成后端"
    assert member.patch(f"/api/contributions/{cid}", json={"quantity": 3}).json()["quantity"] == 3
    confirmed = owner.post(f"/api/contributions/{cid}/confirm", json={"note": "已核对"})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert member.patch(f"/api/contributions/{cid}", json={"title": "不能改"}).status_code == 409

    disputed_item = member.post(f"/api/projects/{pid}/contributions", json={"kind": "document", "title": "文档"}).json()
    disputed = owner.post(f"/api/contributions/{disputed_item['id']}/dispute", json={"note": "缺少材料"})
    assert disputed.json()["status"] == "disputed"
    assert owner.delete(f"/api/contributions/{disputed_item['id']}").status_code == 204
    assert owner.get(f"/api/contributions/{disputed_item['id']}").status_code == 404
    contribution_list = owner.get(f"/api/projects/{pid}/contributions", params={"status": "confirmed"})
    assert contribution_list.json()["total"] == 1

    recommendation = owner.get(f"/api/projects/{pid}/recommendations", params={"task_name": "新的后端任务", "task_type": "后端"})
    assert recommendation.status_code == 200 and "generated_at" in recommendation.json()
    by_task = owner.get(f"/api/projects/{pid}/recommendations", params={"task_id": task["id"]})
    assert by_task.status_code == 200 and by_task.json()["task"]["task_id"] == task["id"]
    assert owner.get(f"/api/projects/{pid}/members/load").status_code == 200
    assert owner.get(f"/api/projects/{pid}/risks").status_code == 200
    weekly = owner.post(f"/api/projects/{pid}/weekly-report")
    assert weekly.status_code == 200 and "summary" in weekly.json()
    weekly_md = owner.get(f"/api/projects/{pid}/weekly-report", params={"format": "markdown"})
    assert weekly_md.status_code == 200 and weekly_md.headers["content-type"].startswith("text/markdown")
    report = owner.get(f"/api/projects/{pid}/report").json()
    assert report["overall"]["tasks_completed"] == 1
    assert report["members"][1]["contributions"] == [{"kind": "code", "quantity": 3.0}]

    markdown = owner.get(f"/api/projects/{pid}/report/export")
    assert markdown.status_code == 200 and markdown.headers["content-type"].startswith("text/markdown")
    pdf = owner.get(f"/api/projects/{pid}/report/export", params={"format": "pdf"})
    assert pdf.status_code == 501 and pdf.json()["error"]["code"] == "NOT_IMPLEMENTED"

    owner.post(f"/api/projects/{pid}/tasks", json={"title": "尚未分配的风险任务", "due_date": "2026-08-26"})
    chat = owner.post(f"/api/projects/{pid}/agent/chat", json={"message": "目前最大的风险是什么？", "session_id": "contract"})
    assert chat.status_code == 200 and chat.json()["source"] == "fallback"
    assert chat.json()["facts"]["project_id"] == pid
    assert chat.json()["generated_at"].endswith("Z")
    sessions = owner.get(f"/api/projects/{pid}/agent/sessions").json()["items"]
    assert sessions[0]["session_id"] == "contract"
    assert owner.delete(f"/api/projects/{pid}/agent/sessions/contract").status_code == 204


def test_forward_migration_preserves_legacy_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT,skills TEXT NOT NULL DEFAULT '[]',max_concurrent_tasks INTEGER NOT NULL DEFAULT 3,status TEXT NOT NULL DEFAULT 'offline',created_at TEXT NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,project_type TEXT,description TEXT,start_date TEXT,end_date TEXT,owner_id INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE contributions (id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,user_id INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT,description TEXT,quantity REAL NOT NULL DEFAULT 1,metadata TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
        INSERT INTO users(name,email,created_at) VALUES ('旧用户','legacy@example.com','2026-08-20T10:00:00+00:00');
        INSERT INTO projects(name,owner_id,created_at) VALUES ('旧项目',1,'2026-08-20T10:00:00+00:00');
        INSERT INTO contributions(project_id,user_id,kind,title,created_at) VALUES (1,1,'code','旧贡献','2026-08-20T12:00:00+00:00');
        """
    )
    conn.commit(); conn.close()
    monkeypatch.setattr(api, "DB_PATH", db_path)
    api.init_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    project = conn.execute("SELECT * FROM projects WHERE id=1").fetchone()
    user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(contributions)").fetchall()}
    contribution = conn.execute("SELECT * FROM contributions WHERE id=1").fetchone()
    conn.close()
    assert project["name"] == "旧项目" and project["status"] == "active" and project["updated_at"].endswith("Z")
    assert user["name"] == "旧用户" and user["created_at"].endswith("Z")
    assert contribution["title"] == "旧贡献" and contribution["status"] == "confirmed"
    assert "status" in columns and "deleted_at" in columns
