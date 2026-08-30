from __future__ import annotations

import hashlib
import hmac
import json

import httpx
from fastapi.testclient import TestClient

import backend.main as api
import backend.routers.integrations as integrations
import backend.routers.integration_platforms as platform_routes
from backend.services.platform_adapters import StandardEvent


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver", follow_redirects=False)


def _setup(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setattr(api, "DB_PATH", tmp_path / name)
    monkeypatch.setenv("GITHUB_TOKEN_SECRET", "integration-test-secret")
    api.init_db()


def _account(client: TestClient, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": "Owner", "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def _project(client: TestClient) -> int:
    return client.post("/api/projects", json={"name": "平台接入项目"}).json()["id"]


class _FakeDocumentAdapter:
    platform = "tencent_doc"

    def configured(self):
        return True

    def fetch_events(self, access_token, config):
        assert access_token == "tencent-token"
        assert config["resource_id"] == "doc-001"
        return [StandardEvent(
            external_id="tencent_doc:doc-001:v2",
            event_type="document_updated",
            title="真实文档版本更新",
            description="由测试替身验证标准事件落库链路",
            evidence_url="https://docs.qq.com/doc-001",
            occurred_at="2026-08-30T08:00:00Z",
            actor="owner",
            payload={"version": "v2"},
        )]


def test_platform_catalog_connection_binding_sync_and_dedup(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "platform-flow.db")
    monkeypatch.setenv("TENCENT_DOC_APP_ID", "app-test")
    monkeypatch.setenv("TENCENT_DOC_API_BASE", "https://docs.example.test/openapi")
    monkeypatch.setitem(integrations.ADAPTERS, "tencent_doc", _FakeDocumentAdapter())
    client = _client(); owner = _account(client, "platform-owner@example.com"); pid = _project(client)

    platforms = client.get("/api/integrations/platforms").json()["items"]
    assert {item["platform"] for item in platforms} >= {"github", "feishu", "tencent_doc"}

    connected = client.post("/api/integrations/tencent_doc/connections", json={
        "access_token": "tencent-token", "external_account_id": "owner-doc", "external_username": "Owner",
    })
    assert connected.status_code == 201 and connected.json()["platform"] == "tencent_doc"
    assert "tencent-token" not in json.dumps(client.get("/api/integrations/connections").json())

    integration = client.post(f"/api/projects/{pid}/integrations", json={
        "platform": "tencent_doc", "resource_type": "document", "resource_id": "doc-001",
        "resource_url": "https://docs.qq.com/doc-001", "api_path": "documents/detail", "actor_user_id": owner["id"],
    })
    assert integration.status_code == 201
    integration_id = integration.json()["id"]

    first = client.post(f"/api/projects/{pid}/integrations/{integration_id}/sync", json={}).json()
    second = client.post(f"/api/projects/{pid}/integrations/{integration_id}/sync", json={}).json()
    assert first["created"] == 1 and first["status"] == "success"
    assert second["created"] == 0 and second["skipped"] == 1

    events = client.get(f"/api/projects/{pid}/integrations/{integration_id}/events").json()
    contributions = client.get(f"/api/projects/{pid}/contributions", params={"source": "tencent_doc"}).json()
    assert events["total"] == 1 and events["items"][0]["event_type"] == "document_updated"
    assert contributions["total"] == 1 and contributions["items"][0]["status"] == "pending"


def _insert_github_connection_and_integration(user_id: int, project_id: int) -> int:
    conn = api.db(); stamp = integrations._now()
    cur = conn.execute(
        "INSERT INTO platform_connections(user_id,platform,external_account_id,external_username,credentials_ref,scopes,status,connected_at,created_at,updated_at) VALUES (?,?,?,?,?,?, 'active',?,?,?)",
        (user_id, "github", "900001", "rxc-test", integrations._encrypt("gh-token"), '["repo"]', stamp, stamp, stamp),
    )
    connection_id = int(cur.lastrowid)
    cur = conn.execute(
        "INSERT INTO project_integrations(project_id,connection_id,platform,config,enabled,created_at,updated_at) VALUES (?,?, 'github',?,1,?,?)",
        (project_id, connection_id, json.dumps({"repos": ["demo/repo"]}), stamp, stamp),
    )
    integration_id = int(cur.lastrowid)
    conn.commit(); conn.close()
    return integration_id


def test_github_webhook_signature_dedup_and_pending_contribution(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "webhook.db")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook-secret")
    client = _client(); owner = _account(client, "webhook-owner@example.com"); pid = _project(client)
    integration_id = _insert_github_connection_and_integration(owner["id"], pid)
    payload = {
        "repository": {"full_name": "demo/repo"},
        "sender": {"login": "rxc-test"},
        "issue": {"title": "Webhook Issue", "html_url": "https://github.com/demo/repo/issues/8"},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "issues", "X-GitHub-Delivery": "delivery-1", "Content-Type": "application/json"}
    first = client.post(f"/api/integrations/github/webhook/{integration_id}", content=body, headers=headers)
    second = client.post(f"/api/integrations/github/webhook/{integration_id}", content=body, headers=headers)
    assert first.status_code == 200 and first.json()["contribution_created"] is True
    assert second.status_code == 200 and second.json()["duplicate"] is True
    bad = client.post(f"/api/integrations/github/webhook/{integration_id}", content=body, headers={**headers, "X-Hub-Signature-256": "sha256=bad"})
    assert bad.status_code == 401
    contributions = client.get(f"/api/projects/{pid}/contributions", params={"source": "github"}).json()
    assert contributions["total"] == 1 and contributions["items"][0]["status"] == "pending"


def test_github_reverse_issue_and_pull_are_explicit_owner_writes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "reverse-write.db")
    client = _client(); owner = _account(client, "reverse-owner@example.com"); pid = _project(client)
    _insert_github_connection_and_integration(owner["id"], pid)
    calls = []

    def fake_post(url, token, payload):
        calls.append((url, token, payload))
        endpoint = "pull" if url.endswith("/pulls") else "issues"
        return httpx.Response(201, json={"number": 9, "html_url": f"https://github.com/demo/repo/{endpoint}/9", "state": "open", "title": payload["title"]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(platform_routes, "_github_post", fake_post)
    issue = client.post(f"/api/projects/{pid}/github/issues", json={"repository": "demo/repo", "title": "从 CollabLedger 建 Issue", "body": "显式确认"})
    pull = client.post(f"/api/projects/{pid}/github/pulls", json={"repository": "demo/repo", "title": "从 CollabLedger 建 PR", "head": "feature", "base": "main", "body": "显式确认"})
    assert issue.status_code == 201 and pull.status_code == 201
    assert calls[0][0].endswith("/issues") and calls[1][0].endswith("/pulls")
    assert all(call[1] == "gh-token" for call in calls)