from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.main as api
import backend.routers.integrations as integrations
import backend.routers.integration_platforms as platform_routes
import backend.services.platform_adapters as platform_adapters
from backend.services.platform_adapters import AdapterError, StandardEvent, TencentDocAdapter


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

    def verify_credentials(self, access_token, open_id):
        return None

    def fetch_events(self, access_token, config):
        assert access_token == "tencent-token"
        assert config["resource_id"] == "doc-001"
        assert config["open_id"] == "owner-doc"
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



def test_tencent_doc_adapter_uses_official_headers_and_file_id_path(monkeypatch):
    monkeypatch.setenv("TENCENT_DOC_APP_ID", "client-id")
    monkeypatch.setenv("TENCENT_DOC_API_BASE", "https://docs.qq.com")
    calls = []
    file_id = "300000000" + chr(36) + "AAAAAAAAAAAA"

    def fake_get(url, *, headers, params=None, timeout=None):
        calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if url.endswith("/util/converter"):
            return httpx.Response(200, json={"ret": 0, "data": {"fileID": file_id}}, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"ret": 0, "data": {
            "ID": file_id,
            "title": "测试文档",
            "url": "https://docs.qq.com/doc/DAAAAAAAAAAAA",
            "lastModifyTime": 1788257462,
            "lastModifyName": "测试用户",
        }}, request=httpx.Request("GET", url))

    monkeypatch.setattr(platform_adapters.httpx, "get", fake_get)
    events = TencentDocAdapter().fetch_events("access-token", {
        "resource_type": "document",
        "resource_id": "DAAAAAAAAAAAA",
        "resource_url": "https://docs.qq.com/doc/DAAAAAAAAAAAA",
        "open_id": "open-id",
    })
    assert len(events) == 1 and events[0].title == "测试文档"
    assert calls[0]["params"] == {"type": 2, "value": "DAAAAAAAAAAAA"}
    assert calls[1]["url"] == f"https://docs.qq.com/openapi/drive/v2/files/{file_id}/metadata"
    assert calls[1]["headers"] == {
        "Accept": "application/json",
        "Access-Token": "access-token",
        "Client-Id": "client-id",
        "Open-Id": "open-id",
    }


def test_tencent_doc_adapter_rejects_untrusted_base_and_path(monkeypatch):
    monkeypatch.setenv("TENCENT_DOC_APP_ID", "client-id")
    calls = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("不应向非允许的腾讯文档请求目标发起请求")

    monkeypatch.setattr(platform_adapters.httpx, "get", fake_get)
    monkeypatch.setenv("TENCENT_DOC_API_BASE", "https://evil.example")
    with pytest.raises(AdapterError, match="API 根地址"):
        TencentDocAdapter().fetch_events("access-token", {"resource_id": "file-id", "open_id": "open-id"})
    assert not calls

    monkeypatch.setenv("TENCENT_DOC_API_BASE", "https://docs.qq.com")
    with pytest.raises(AdapterError, match="仅允许官方查询文档元信息接口"):
        TencentDocAdapter().fetch_events("access-token", {
            "resource_id": "file-id",
            "open_id": "open-id",
            "api_path": "/openapi/drive/v2/files/{fileID}/collaborators",
        })
    assert not calls

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


def test_document_sync_does_not_use_another_users_connection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "connection-owner.db")
    monkeypatch.setenv("TENCENT_DOC_APP_ID", "client-id")
    monkeypatch.setenv("TENCENT_DOC_API_BASE", "https://docs.qq.com")
    adapter_calls = []

    class GuardAdapter:
        platform = "tencent_doc"

        def configured(self):
            return True

        def verify_credentials(self, access_token, open_id):
            return None

        def fetch_events(self, access_token, config):
            adapter_calls.append((access_token, config))
            return []

    monkeypatch.setitem(integrations.ADAPTERS, "tencent_doc", GuardAdapter())
    owner_client = _client()
    owner = _account(owner_client, "connection-owner@example.com")
    project_id = _project(owner_client)
    connected = owner_client.post("/api/integrations/tencent_doc/connections", json={
        "access_token": "owner-token",
        "external_account_id": "owner-open-id",
        "external_username": "Owner",
    })
    assert connected.status_code == 201
    integration = owner_client.post(f"/api/projects/{project_id}/integrations", json={
        "platform": "tencent_doc",
        "resource_type": "document",
        "resource_id": "doc-001",
        "actor_user_id": owner["id"],
    })
    assert integration.status_code == 201
    integration_id = integration.json()["id"]

    replacement_client = _client()
    replacement = _account(replacement_client, "replacement-owner@example.com")
    assert owner_client.post(f"/api/projects/{project_id}/members", json={
        "user_id": replacement["id"], "role": "member",
    }).status_code == 201
    replacement_id = replacement["id"]
    owner_id = owner["id"]
    assert owner_client.patch(f"/api/projects/{project_id}/members/{replacement_id}", json={"role": "owner"}).status_code == 200
    assert owner_client.patch(f"/api/projects/{project_id}/members/{owner_id}", json={"role": "member"}).status_code == 200

    response = replacement_client.post(f"/api/projects/{project_id}/integrations/{integration_id}/sync", json={})
    assert response.status_code == 502
    assert "平台连接已断开" in response.json()["error"]["message"]
    assert adapter_calls == []

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


def test_github_contract_binding_returns_legacy_fields_and_rejects_external_urls(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "github-contract.db")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    owner_client = _client(); _account(owner_client, "github-contract@example.com")
    project_id = _project(owner_client)
    state = owner_client.get("/api/integrations/github/auth-url").json()["state"]
    monkeypatch.setattr(integrations, "_exchange_github_identity", lambda code, redirect_uri: integrations.PlatformIdentity("1", "owner", "gh-token", ["repo"]))
    assert owner_client.get("/api/integrations/github/callback", params={"code": "code", "state": state}).status_code == 307

    invalid = owner_client.post(f"/api/projects/{project_id}/github/repositories", json={"repository_url": "https://evil.example/repo"})
    assert invalid.status_code == 422
    bound = owner_client.post(
        f"/api/projects/{project_id}/github/repositories",
        json={"repository_url": "https://github.com/demo/repo.git", "default_branch": "trunk", "sync_from": "2026-08-30"},
    )
    assert bound.status_code == 201
    body = bound.json()
    assert body["repository_url"] == "https://github.com/demo/repo.git"
    assert body["default_branch"] == "trunk"
    assert body["sync_from"] == "2026-08-30"


def test_github_statistics_filters_repository_and_includes_end_date(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "github-statistics-contract.db")
    client = _client(); user = _account(client, "github-statistics@example.com"); project_id = _project(client)
    integration_id = _insert_github_connection_and_integration(user["id"], project_id)
    conn = api.db(); stamp = integrations._now()
    for repo, occurred_at in (("demo/repo", "2026-08-30T23:59:00Z"), ("other/repo", "2026-08-30T12:00:00Z")):
        conn.execute(
            """INSERT INTO contributions(project_id,user_id,kind,title,description,quantity,metadata,evidence_url,status,source,occurred_at,created_at,updated_at,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, user["id"], "code", f"提交：{repo} commit", "同步", 1,
             json.dumps({"github": {"repo": repo, "additions": 4, "deletions": 2}}), None, "pending", "github", occurred_at, stamp, stamp, user["id"]),
        )
    conn.commit(); conn.close()

    all_stats = client.get(f"/api/projects/{project_id}/github/statistics", params={"end_date": "2026-08-30"}).json()
    filtered = client.get(
        f"/api/projects/{project_id}/github/statistics",
        params={"repository_id": integration_id, "end_date": "2026-08-30"},
    ).json()
    assert all_stats["members"][0]["commits"] == 2
    assert filtered["members"][0]["commits"] == 1
    assert filtered["members"][0]["additions"] == 4


def test_tencent_doc_connection_rejects_invalid_credentials(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "connection-invalid.db")

    class RejectAdapter:
        platform = "tencent_doc"

        def configured(self):
            return True

        def verify_credentials(self, access_token, open_id):
            raise AdapterError("腾讯文档 API 错误（20103）：token 无效")

    monkeypatch.setitem(integrations.ADAPTERS, "tencent_doc", RejectAdapter())
    client = _client()
    _account(client, "connection-invalid@example.com")
    rejected = client.post("/api/integrations/tencent_doc/connections", json={
        "access_token": "bad-token",
        "external_account_id": "owner-open-id",
        "external_username": "Owner",
    })
    assert rejected.status_code == 502
    assert "token 无效" in rejected.json()["error"]["message"]
    listed = client.get("/api/integrations/connections").json()
    assert all(item["platform"] != "tencent_doc" for item in listed["items"])