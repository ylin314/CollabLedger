from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

import backend.main as api
from backend.audit import write_audit
from backend.rate_limit import LimitRule, SlidingWindowLimiter


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def test_sliding_window_limiter_retry_after():
    limiter = SlidingWindowLimiter({"/login": LimitRule(2, 10)})
    assert limiter.check("ip", "/login", 0)[0] is True
    assert limiter.check("ip", "/login", 1)[0] is True
    allowed, count, retry = limiter.check("ip", "/login", 2)
    assert allowed is False and count == 2 and retry == 8
    assert limiter.check("ip", "/login", 10.1)[0] is True


def test_audit_writer_and_http_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "audit.db")
    api.init_db()
    conn = api.db()
    write_audit(conn, action="unit.test", actor_id=None, resource_type="test", after={"ok": True})
    conn.commit()
    assert json.loads(conn.execute("SELECT after_data FROM audit_logs WHERE action='unit.test'").fetchone()[0]) == {"ok": True}
    conn.close()

    client = TestClient(api.app, base_url="https://testserver")
    _account(client, "Owner", "audit-owner@example.com")
    project = client.post("/api/projects", json={"name": "审计项目"})
    assert project.status_code == 201
    conn = api.db()
    row = conn.execute("SELECT * FROM audit_logs WHERE request_path='/api/projects' ORDER BY id DESC").fetchone()
    conn.close()
    assert row is not None and row["actor_id"] is not None and row["status_code"] == 201
    actor_id = row["actor_id"]
    assert client.post("/api/auth/logout").status_code == 204
    conn = api.db()
    logout_audit = conn.execute("SELECT actor_id FROM audit_logs WHERE request_path='/api/auth/logout' ORDER BY id DESC").fetchone()
    conn.close()
    assert logout_audit is not None and logout_audit["actor_id"] == actor_id


def test_viewer_cannot_write_agent_or_delete_session(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "permissions.db")
    api.init_db()
    owner = TestClient(api.app, base_url="https://testserver")
    viewer = TestClient(api.app, base_url="https://testserver")
    _account(owner, "Owner", "security-owner@example.com")
    _account(viewer, "Viewer", "security-viewer@example.com")
    project_id = owner.post("/api/projects", json={"name": "权限项目"}).json()["id"]
    code = owner.post(f"/api/projects/{project_id}/invitations", json={"role": "viewer"}).json()["code"]
    assert viewer.post(f"/api/invitations/{code}/accept").status_code == 200
    assert viewer.post(f"/api/projects/{project_id}/agent/chat", json={"message": "项目如何"}).status_code == 403
    assert viewer.delete(f"/api/projects/{project_id}/agent/sessions/default").status_code == 403


def test_agent_config_never_returns_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "agent-config.db")
    monkeypatch.setenv("LLM_API_KEY", "sk-secret-value-that-must-not-leak")
    api.init_db()
    client = TestClient(api.app, base_url="https://testserver")
    _account(client, "Owner", "config-owner@example.com")
    payload = client.get("/api/agent/config").json()
    assert "api_key" not in payload and "api_key_masked" not in payload
    assert "secret-value" not in json.dumps(payload)


def test_password_storage_and_cors_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "cors.db")
    api.init_db()
    client = TestClient(api.app, base_url="https://testserver")
    password = "password-123"
    response = client.post("/api/auth/register", json={"name": "Secure", "email": "secure@example.com", "password": password})
    assert response.status_code == 201 and "password" not in json.dumps(response.json())
    conn = api.db()
    stored = conn.execute("SELECT password_hash FROM users WHERE email=?", ("secure@example.com",)).fetchone()["password_hash"]
    conn.close()
    assert stored != password and stored.startswith("pbkdf2_sha256$")

    allowed = client.options(
        "/api/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    denied = client.options(
        "/api/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert denied.headers.get("access-control-allow-origin") is None
