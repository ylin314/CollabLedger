from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

import backend.main as api
import backend.routers.integrations as gi


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver", follow_redirects=False)


def _account(client: TestClient, name: str, email: str) -> dict:
    assert client.post("/api/auth/register", json={"name": name, "email": email, "password": "password-123"}).status_code == 201
    return client.post("/api/auth/login", json={"email": email, "password": "password-123"}).json()["user"]


def _setup(monkeypatch, tmp_path, filename: str):
    monkeypatch.setattr(api, "DB_PATH", tmp_path / filename)
    api.init_db()
    monkeypatch.setattr(gi, "_pending_states", {})
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid-test")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret-test")
    monkeypatch.setenv("COLLAB_FRONTEND_BASE", "https://testserver")


def _project(client: TestClient) -> int:
    return client.post("/api/projects", json={"name": "GitHub 接入项目"}).json()["id"]


def _fake_github(monkeypatch, *, commits=None, pulls=None, fail=False):
    """fail=True 只让 commits/pulls 抛网络错误；OAuth 换 token 与 /user 始终可用。"""

    def fake_post(url, **kwargs):
        assert url.endswith("/access_token")
        return httpx.Response(200, json={"access_token": "gh-token", "token_type": "bearer"}, request=httpx.Request("POST", url))

    def fake_get(url, headers=None, params=None, **kwargs):
        if fail and (url.endswith("/commits") or url.endswith("/pulls")):
            raise httpx.ConnectError("network down")
        if url.endswith("/user"):
            return httpx.Response(200, json={"id": 900001, "login": "rxc-test"}, request=httpx.Request("GET", url))
        if url.endswith("/commits"):
            return httpx.Response(200, json=commits if commits is not None else [], request=httpx.Request("GET", url))
        if url.endswith("/pulls"):
            return httpx.Response(200, json=pulls if pulls is not None else [], request=httpx.Request("GET", url))
        raise AssertionError(url)

    monkeypatch.setattr(gi.httpx, "post", fake_post)
    monkeypatch.setattr(gi.httpx, "get", fake_get)


_COMMIT = [{
    "sha": "a" * 40,
    "html_url": "https://github.com/demo/repo/commit/" + "a" * 40,
    "author": {"login": "rxc-test"},
    "commit": {"message": "feat:增加真实提交\n\nbody", "author": {"date": "2026-08-28T00:00:00Z", "email": "rxc@example.com"}},
}]
_PULL = [{
    "number": 7,
    "title": "feat:接入 GitHub 同步",
    "html_url": "https://github.com/demo/repo/pull/7",
    "user": {"login": "rxc-test"},
    "merged_by": {"login": "leader-test"},
    "merged_at": "2026-08-28T01:00:00Z",
    "created_at": "2026-08-27T01:00:00Z",
}]


def test_auth_url_requires_config_or_returns_state(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "auth-url.db")
    owner = _client(); user = _account(owner, "Owner", "owner-gh@example.com")
    payload = owner.get("/api/integrations/github/auth-url").json()
    assert payload["configured"] is True
    assert payload["url"].startswith("https://github.com/login/oauth/authorize?")
    assert payload["state"] in gi._pending_states

    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    payload = owner.get("/api/integrations/github/auth-url").json()
    assert payload["configured"] is False and "未配置" in payload["message"]


def test_callback_rejects_invalid_state_and_connects_on_success(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "callback.db")
    _fake_github(monkeypatch)
    owner = _client(); user = _account(owner, "Owner", "callback-owner@example.com")

    bad = owner.get("/api/integrations/github/callback", params={"code": "abc", "state": "bad-state"})
    assert bad.status_code == 307 and "invalid_state" in bad.headers["location"]

    created = owner.get("/api/integrations/github/auth-url").json()
    ok = owner.get("/api/integrations/github/callback", params={"code": "abc", "state": created["state"]})
    assert ok.status_code == 307 and "connected=rxc-test" in ok.headers["location"]

    status = owner.get("/api/integrations/github/status").json()
    assert status["connected"] is True and status["account"] == "900001"
    assert "gh-token" not in json.dumps(status)


def test_sync_creates_deduplicates_and_respects_permission(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "sync.db")
    _fake_github(monkeypatch, commits=_COMMIT, pulls=_PULL)
    owner, outsider = _client(), _client()
    owner_user = _account(owner, "组长", "sync-owner@example.com")
    _account(outsider, "外人", "sync-outsider@example.com")
    pid = _project(owner)

    created_state = owner.get("/api/integrations/github/auth-url").json()
    assert owner.get("/api/integrations/github/callback", params={"code": "abc", "state": created_state["state"]}).status_code == 307

    assert outsider.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": ["demo/repo"]}}).status_code == 403

    config = {"repos": ["demo/repo"], "logins": {"rxc-test": owner_user["id"]}}
    first = owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": config}).json()
    assert first["created"] == 2 and first["skipped"] == 0

    items = owner.get(f"/api/projects/{pid}/contributions", params={"source": "github"}).json()["items"]
    assert len(items) == 2
    commit_item = next(item for item in items if item["title"].startswith("提交："))
    pull_item = next(item for item in items if item["title"].startswith("PR："))
    assert commit_item["status"] == "pending" and commit_item["evidence_url"].endswith("a" * 40)
    assert pull_item["evidence_url"].endswith("/pull/7")

    second = owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": config}).json()
    assert second["created"] == 0 and second["skipped"] == 2
    assert len(owner.get(f"/api/projects/{pid}/contributions", params={"source": "github"}).json()["items"]) == 2


def test_sync_failure_records_sync_job_error(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "failure.db")
    _fake_github(monkeypatch, fail=True)
    owner = _client(); user = _account(owner, "组长", "fail-owner@example.com")
    pid = _project(owner)
    state = owner.get("/api/integrations/github/auth-url").json()
    owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state["state"]})
    assert owner.get("/api/integrations/github/status").json()["connected"] is True

    response = owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": ["demo/repo"]}})
    assert response.status_code == 502
    assert "GitHub 同步失败" in response.json()["error"]["message"]

    conn = api.db()
    row = conn.execute("SELECT status,error FROM sync_jobs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["status"] == "failed" and "network down" in row["error"]


def test_disconnect_removes_connection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "disconnect.db")
    _fake_github(monkeypatch)
    owner = _client(); _account(owner, "组长", "disconnect-owner@example.com")
    state = owner.get("/api/integrations/github/auth-url").json()
    owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state["state"]})
    assert owner.get("/api/integrations/github/status").json()["connected"] is True
    assert owner.post("/api/integrations/github/disconnect").status_code == 200
    status = owner.get("/api/integrations/github/status").json()
    assert status["connected"] is False and status["projects"] == []