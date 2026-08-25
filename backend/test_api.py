from __future__ import annotations

from fastapi.testclient import TestClient

import backend.main as api


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver")


def _register_login(client: TestClient, name: str, email: str) -> dict:
    registered = client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"})
    assert registered.status_code == 201, registered.text
    assert registered.json()["status"] == "offline"
    logged = client.post("/api/auth/login", json={"email": email, "password": "password-123"})
    assert logged.status_code == 200, logged.text
    return logged.json()["user"]


def test_cookie_auth_errors_profile_and_project_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "contract-auth.db")
    api.init_db()

    anonymous = _client()
    unauthenticated = anonymous.get("/api/projects")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"error": {"code": "UNAUTHORIZED", "message": "请先登录"}}

    owner = _client()
    user = _register_login(owner, "张三", "zhangsan@example.com")
    cookie = owner.cookies.get("collab_session")
    assert cookie
    set_cookie = owner.post("/api/auth/login", json={"email": "zhangsan@example.com", "password": "password-123"}).headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=lax" in set_cookie

    me = owner.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["id"] == user["id"]
    patched = owner.patch("/api/users/me", json={"skills": ["Python", "后端"], "max_concurrent_tasks": 4, "status": "online"})
    assert patched.json()["skills"] == ["Python", "后端"]

    project = owner.post(
        "/api/projects",
        json={"name": "协作账本", "project_type": "课程项目", "start_date": "2026-09-01", "end_date": "2026-12-20"},
    )
    assert project.status_code == 201, project.text
    pid = project.json()["id"]
    assert project.json()["current_user_role"] == "owner"
    assert project.json()["created_at"].endswith("Z")

    listing = owner.get("/api/projects", params={"page": 1, "page_size": 1})
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["role"] == "owner"

    archived = owner.post(f"/api/projects/{pid}/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert owner.post(f"/api/projects/{pid}/tasks", json={"title": "只读项目不能新增"}).status_code == 409
    assert owner.get("/api/projects", params={"archived": True}).json()["total"] == 1
    restored = owner.post(f"/api/projects/{pid}/restore")
    assert restored.status_code == 200 and restored.json()["status"] == "active"

    duplicate = owner.post("/api/auth/register", json={"name": "重复", "email": "zhangsan@example.com", "password": "password-123"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "CONFLICT"
    invalid = owner.post("/api/auth/register", json={"name": "", "email": "bad", "password": "1"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    logged_out = owner.post("/api/auth/logout")
    assert logged_out.status_code == 204
    assert owner.get("/api/auth/me").status_code == 401
