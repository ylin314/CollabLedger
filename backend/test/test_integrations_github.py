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
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid-test")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret-test")
    monkeypatch.setenv("COLLAB_FRONTEND_BASE", "https://testserver")


def _project(client: TestClient) -> int:
    return client.post("/api/projects", json={"name": "GitHub 接入项目"}).json()["id"]


def _fake_github(monkeypatch, *, commits=None, pulls=None, issues=None, reviews=None, fail=False, fail_repos=()):
    """fail=True 只让 commits/pulls 抛网络错误；OAuth 换 token 与 /user 始终可用。"""

    def fake_post(url, **kwargs):
        assert url.endswith("/access_token")
        return httpx.Response(200, json={"access_token": "gh-token", "token_type": "bearer"}, request=httpx.Request("POST", url))

    def fake_get(url, headers=None, params=None, **kwargs):
        should_fail = fail or any(f"/repos/{repo}/" in url for repo in fail_repos)
        if should_fail and (url.endswith("/commits") or url.endswith("/pulls") or url.endswith("/issues") or url.endswith("/reviews")):
            raise httpx.ConnectError("network down")
        if url.endswith("/user"):
            return httpx.Response(200, json={"id": 900001, "login": "rxc-test"}, request=httpx.Request("GET", url))
        if url.endswith("/commits"):
            return httpx.Response(200, json=commits if commits is not None else [], request=httpx.Request("GET", url))
        if url.endswith("/pulls"):
            return httpx.Response(200, json=pulls if pulls is not None else [], request=httpx.Request("GET", url))
        if url.endswith("/issues"):
            return httpx.Response(200, json=issues if issues is not None else [], request=httpx.Request("GET", url))
        if url.endswith("/reviews"):
            return httpx.Response(200, json=reviews if reviews is not None else [], request=httpx.Request("GET", url))
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
    conn = api.db()
    persisted = conn.execute("SELECT user_id,platform,consumed_at FROM oauth_states WHERE state=?", (payload["state"],)).fetchone()
    conn.close()
    assert persisted["user_id"] == user["id"] and persisted["platform"] == "github" and persisted["consumed_at"] is None

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
    assert status["connected"] is True and status["account"] == "rxc-test"
    assert "gh-token" not in json.dumps(status)
    conn = api.db()
    stored = conn.execute("SELECT credentials_ref FROM platform_connections WHERE user_id=?", (user["id"],)).fetchone()["credentials_ref"]
    consumed = conn.execute("SELECT consumed_at FROM oauth_states WHERE state=?", (created["state"],)).fetchone()["consumed_at"]
    conn.close()
    assert stored != "gh-token" and "gh-token" not in stored and consumed
    replay = owner.get("/api/integrations/github/callback", params={"code": "abc", "state": created["state"]})
    assert "invalid_state" in replay.headers["location"]


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
    assert row["status"] == "failed" and "网络请求失败" in row["error"] and "network down" not in row["error"]


def test_disconnect_revokes_connection_and_preserves_history(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "disconnect.db")
    _fake_github(monkeypatch)
    owner = _client(); _account(owner, "组长", "disconnect-owner@example.com")
    state = owner.get("/api/integrations/github/auth-url").json()
    owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state["state"]})
    assert owner.get("/api/integrations/github/status").json()["connected"] is True
    pid = _project(owner)
    owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": ["demo/repo"]}})
    assert owner.post("/api/integrations/github/disconnect").status_code == 200
    status = owner.get("/api/integrations/github/status").json()
    assert status["connected"] is False and status["projects"] == []
    conn = api.db()
    connection = conn.execute("SELECT status,credentials_ref FROM platform_connections ORDER BY id DESC LIMIT 1").fetchone()
    integration_count = conn.execute("SELECT COUNT(*) n FROM project_integrations").fetchone()["n"]
    event_count = conn.execute("SELECT COUNT(*) n FROM external_events").fetchone()["n"]
    conn.close()
    assert connection["status"] == "revoked" and connection["credentials_ref"] is None
    assert integration_count == 1 and event_count == 0

def test_callback_missing_params_redirects_with_flag(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "callback-missing.db")
    owner = _client(); _account(owner, "组长", "cb-missing@example.com")
    no_params = owner.get("/api/integrations/github/callback")
    assert no_params.status_code == 307 and "missing_code_or_state" in no_params.headers["location"]
    no_code = owner.get("/api/integrations/github/callback", params={"state": "whatever"})
    assert no_code.status_code == 307 and "missing_code_or_state" in no_code.headers["location"]
    error = owner.get("/api/integrations/github/callback", params={"code": "abc", "state": "s", "error": "access_denied"})
    assert error.status_code == 307 and "error=access_denied" in error.headers["location"]


def test_status_not_connected_before_oauth(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "status-notconnected.db")
    owner = _client(); _account(owner, "组长", "st-nc@example.com")
    status = owner.get("/api/integrations/github/status").json()
    assert status["connected"] is False and status["projects"] == []


def test_sync_requires_connection_and_repos(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "sync-requires.db")
    owner = _client(); _account(owner, "组长", "sync-req@example.com")
    pid = _project(owner)
    not_connected = owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": ["demo/repo"]}})
    assert not_connected.status_code == 400
    assert "尚未连接 GitHub" in not_connected.json()["error"]["message"]

    _fake_github(monkeypatch)
    state = owner.get("/api/integrations/github/auth-url").json()
    owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state["state"]})
    empty = owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": []}})
    assert empty.status_code == 422


def test_disconnect_then_status_not_connected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "disconnect-status.db")
    _fake_github(monkeypatch)
    owner = _client(); _account(owner, "组长", "dc-st@example.com")
    state = owner.get("/api/integrations/github/auth-url").json()
    owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state["state"]})
    assert owner.get("/api/integrations/github/status").json()["connected"] is True
    assert owner.post("/api/integrations/github/disconnect").status_code == 200
    status = owner.get("/api/integrations/github/status").json()
    assert status["connected"] is False and status["projects"] == []


def test_oauth_state_is_user_bound_and_survives_memory_reset(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "state-bound.db")
    _fake_github(monkeypatch)
    owner, other = _client(), _client()
    _account(owner, "Owner", "state-owner@example.com")
    _account(other, "Other", "state-other@example.com")
    state = owner.get("/api/integrations/github/auth-url").json()["state"]
    wrong_user = other.get("/api/integrations/github/callback", params={"code": "abc", "state": state})
    assert "invalid_state" in wrong_user.headers["location"]
    correct_user = owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state})
    assert "connected=rxc-test" in correct_user.headers["location"]


def test_fernet_secret_change_cannot_decrypt(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN_SECRET", "first-secret")
    encrypted = gi._encrypt("sensitive-token")
    assert "sensitive-token" not in encrypted
    monkeypatch.setenv("GITHUB_TOKEN_SECRET", "second-secret")
    assert gi._decrypt(encrypted) is None


def test_member_cannot_trigger_owner_sync(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "member-sync.db")
    _fake_github(monkeypatch)
    owner, member = _client(), _client()
    owner_user = _account(owner, "Owner", "owner-member-sync@example.com")
    member_user = _account(member, "Member", "member-sync@example.com")
    pid = _project(owner)
    conn = api.db()
    conn.execute("INSERT INTO memberships(project_id,user_id,role,joined_at,status,updated_at) VALUES (?,?,'member',?,'active',?)", (pid, member_user["id"], gi._now(), gi._now()))
    conn.commit(); conn.close()
    state = member.get("/api/integrations/github/auth-url").json()["state"]
    member.get("/api/integrations/github/callback", params={"code": "abc", "state": state})
    response = member.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": ["demo/repo"]}})
    assert response.status_code == 403


def test_sync_preserves_partial_success(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "partial-sync.db")
    _fake_github(monkeypatch, commits=_COMMIT, pulls=[], fail_repos=("bad/repo",))
    owner = _client(); user = _account(owner, "Owner", "partial-owner@example.com")
    pid = _project(owner)
    state = owner.get("/api/integrations/github/auth-url").json()["state"]
    owner.get("/api/integrations/github/callback", params={"code": "abc", "state": state})
    response = owner.post(f"/api/projects/{pid}/integrations/github/sync", json={"config": {"repos": ["demo/repo", "bad/repo"], "logins": {"rxc-test": user["id"]}}})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial" and payload["created"] == 1 and len(payload["errors"]) == 1
    conn = api.db()
    job = conn.execute("SELECT status,error FROM sync_jobs ORDER BY id DESC LIMIT 1").fetchone()
    contributions = conn.execute("SELECT COUNT(*) n FROM contributions WHERE project_id=? AND source='github'", (pid,)).fetchone()["n"]
    conn.close()
    assert job["status"] == "partial" and contributions == 1


def test_production_requires_token_secret(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "prod-secret.db")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("GITHUB_TOKEN_SECRET", raising=False)
    owner = _client(); _account(owner, "Owner", "prod-secret@example.com")
    payload = owner.get("/api/integrations/github/auth-url").json()
    assert payload["configured"] is False and "GITHUB_TOKEN_SECRET" in payload["message"]


def test_oauth_state_is_bound_to_exact_login_session(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "state-session.db")
    _fake_github(monkeypatch)
    first = _client(); user = _account(first, "Owner", "session-bound@example.com")
    second = _client()
    assert second.post("/api/auth/login", json={"email": "session-bound@example.com", "password": "password-123"}).status_code == 200
    state = first.get("/api/integrations/github/auth-url").json()["state"]
    wrong_session = second.get("/api/integrations/github/callback", params={"code": "abc", "state": state})
    assert "invalid_state" in wrong_session.headers["location"]
    correct_session = first.get("/api/integrations/github/callback", params={"code": "abc", "state": state})
    assert "connected=rxc-test" in correct_session.headers["location"]
