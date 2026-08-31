"""D5 外部平台接入（GitHub OAuth + 单向同步）。

只做 GitHub 一项；飞书 / 腾讯文档仅记录 TODO。复用 platform_connections / project_integrations /
external_events / sync_jobs 四张既有表，不建新表。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from backend.core.context import *
from backend.auth import request_token, token_digest
from backend.schemas import *
from backend.services.platform_adapters import ADAPTERS, AdapterError, PlatformIdentity

router = APIRouter()

GITHUB_API = "https://api.github.com"
GITHUB_OAUTH = "https://github.com/login/oauth"
STATE_TTL_SECONDS = 600
HTTP_TIMEOUT = 15.0
# 单次同步最多为多少条新 commit 请求 diff 统计；超过则跳过，避免首次全量同步耗尽 API 配额。
DIFF_DETAIL_LIMIT = 100
FRONTEND_BASE = os.getenv("COLLAB_FRONTEND_BASE", "http://127.0.0.1:5173")

def _now() -> str: return now_iso()

# ---------- token 加密存储 ----------
def _fernet() -> Fernet:
    secret = (os.getenv("GITHUB_TOKEN_SECRET") or "").strip()
    environment = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if not secret and environment in {"prod", "production"}:
        raise RuntimeError("GITHUB_TOKEN_SECRET 未配置，生产环境拒绝使用 GitHub OAuth")
    # 开发/测试允许稳定开发密钥，生产必须显式注入随机密钥。
    secret = secret or "collab-ledger-local-development-only"
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def _decrypt(blob: Optional[str]) -> Optional[str]:
    if not blob:
        return None
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None


def _state_expired(stamp: str) -> bool:
    try:
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) >= created


def _session_hash(request: Request) -> str:
    token = request_token(request)
    if not token:
        raise RuntimeError("当前登录会话无效")
    return token_digest(token)


def _store_state(user_id: int, state: str, redirect_uri: str, session_hash: str, platform: str = "github") -> None:
    conn = db(); stamp = _now(); expires = (datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL_SECONDS)).isoformat().replace("+00:00", "Z")
    conn.execute("DELETE FROM oauth_states WHERE expires_at<? OR consumed_at IS NOT NULL", (stamp,))
    conn.execute("INSERT INTO oauth_states(state,user_id,platform,session_hash,redirect_uri,expires_at,created_at) VALUES (?,?,?,?,?,?,?)", (state, user_id, platform, session_hash, redirect_uri, expires, stamp))
    conn.commit(); conn.close()


def _consume_state(state: str, user_id: int, session_hash: str, platform: str = "github") -> Optional[dict[str, Any]]:
    conn = db()
    row = conn.execute(
        "SELECT state,redirect_uri,expires_at FROM oauth_states WHERE state=? AND user_id=? AND session_hash=? AND platform=? AND consumed_at IS NULL",
        (state, user_id, session_hash, platform),
    ).fetchone()
    if not row or _state_expired(row["expires_at"]):
        conn.close()
        return None
    cur = conn.execute(
        "UPDATE oauth_states SET consumed_at=? WHERE state=? AND user_id=? AND session_hash=? AND platform=? AND consumed_at IS NULL",
        (_now(), state, user_id, session_hash, platform),
    )
    conn.commit()
    result = dict(row) if cur.rowcount == 1 else None
    conn.close()
    return result

def _github_config() -> dict[str, Optional[str]]:
    return {
        "client_id": os.getenv("GITHUB_CLIENT_ID") or None,
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET") or None,
        "redirect_uri": os.getenv("GITHUB_REDIRECT_URI") or "http://127.0.0.1:8000/api/integrations/github/callback",
    }


def _exchange_github_identity(code: str, redirect_uri: str) -> PlatformIdentity:
    cfg = _github_config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise AdapterError("GitHub OAuth 未配置")
    try:
        token_resp = httpx.post(
            f"{GITHUB_OAUTH}/access_token",
            json={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise AdapterError("GitHub token 交换失败")
        profile = _github_get(f"{GITHUB_API}/user", access_token)
        profile.raise_for_status()
        user = profile.json()
    except httpx.HTTPError as exc:
        raise AdapterError("GitHub API 请求失败") from exc
    if not user.get("id") or not user.get("login"):
        raise AdapterError("GitHub 未返回有效用户身份")
    return PlatformIdentity(str(user["id"]), str(user["login"]), str(access_token), ["repo", "read:org"])

def _connection(conn: sqlite3.Connection, user_id: int, platform: str = "github") -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM platform_connections WHERE user_id=? AND platform=? AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id, platform),
    ).fetchone()

def _integration(conn: sqlite3.Connection, project_id: int, connection_id: int, platform: str = "github") -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM project_integrations WHERE project_id=? AND connection_id=? AND platform=? LIMIT 1",
        (project_id, connection_id, platform),
    ).fetchone()

def _config_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}

def _github_get(url: str, token: str, params: Optional[dict[str, Any]] = None) -> httpx.Response:
    return httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        params=params,
        timeout=HTTP_TIMEOUT,
    )


def _github_get_items(url: str, token: str, params: Optional[dict[str, Any]] = None, *, max_pages: int = 10) -> list[dict[str, Any]]:
    base_params = dict(params or {})
    per_page = int(base_params.pop("per_page", 100))
    items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        response = _github_get(url, token, {**base_params, "per_page": per_page, "page": page})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("GitHub 列表接口返回格式不正确")
        items.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < per_page:
            break
    return items


def _github_post(url: str, token: str, payload: dict[str, Any]) -> httpx.Response:
    return httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json=payload,
        timeout=HTTP_TIMEOUT,
    )


def _commit_diff_stats(token: str, repo: str, sha: str) -> Optional[tuple[int, int]]:
    """请求单条 commit 详情获取增删行数；失败返回 None，不阻塞整体同步。"""
    try:
        response = _github_get(f"{GITHUB_API}/repos/{repo}/commits/{sha}", token)
        response.raise_for_status()
        stats = (response.json().get("stats") or {})
        return int(stats.get("additions") or 0), int(stats.get("deletions") or 0)
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _safe_external_error(response: httpx.Response) -> str:
    return f"外部平台返回 HTTP {response.status_code}"


def _integration_public(row: sqlite3.Row) -> dict[str, Any]:
    config = _config_dict(row["config"])
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "platform": row["platform"],
        "resource_type": config.get("resource_type", "repository"),
        "resource_id": config.get("resource_id") or (config.get("repos") or [None])[0],
        "resource_url": config.get("resource_url"),
        "enabled": bool(row["enabled"]),
        "last_synced_at": config.get("last_synced_at"),
        "created_at": row["created_at"],
    }


def _upsert_connection(conn: sqlite3.Connection, user_id: int, platform: str, identity: PlatformIdentity) -> int:
    existing = conn.execute(
        "SELECT * FROM platform_connections WHERE user_id=? AND platform=? ORDER BY id DESC LIMIT 1",
        (user_id, platform),
    ).fetchone()
    stamp = _now()
    encrypted = _encrypt(identity.access_token)
    scopes = json.dumps(identity.scopes, ensure_ascii=False)
    if existing:
        conn.execute(
            "UPDATE platform_connections SET external_account_id=?,external_username=?,credentials_ref=?,scopes=?,status='active',connected_at=COALESCE(connected_at,?),updated_at=? WHERE id=?",
            (identity.external_account_id, identity.external_username, encrypted, scopes, stamp, stamp, existing["id"]),
        )
        connection_id = int(existing["id"])
    else:
        cur = conn.execute(
            "INSERT INTO platform_connections(user_id,platform,external_account_id,external_username,credentials_ref,scopes,status,connected_at,created_at,updated_at) VALUES (?,?,?,?,?,?, 'active',?,?,?)",
            (user_id, platform, identity.external_account_id, identity.external_username, encrypted, scopes, stamp, stamp, stamp),
        )
        connection_id = int(cur.lastrowid)
    conn.commit()
    return connection_id



def _insert_job(conn: sqlite3.Connection, integration_id: int) -> int:
    stamp = _now()
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "UPDATE sync_jobs SET status='failed',error='服务重启或任务超时，已标记为可重试',finished_at=? WHERE integration_id=? AND status='running' AND started_at<?",
        (stamp, integration_id, stale_before),
    )
    cur = conn.execute(
        "INSERT INTO sync_jobs(integration_id,status,started_at,created_at) VALUES (?, 'running', ?, ?)",
        (integration_id, stamp, stamp),
    )
    conn.commit()
    return int(cur.lastrowid)

def _finish_job(conn: sqlite3.Connection, job_id: int, *, error: Optional[str] = None, status: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE sync_jobs SET status=?, error=?, finished_at=? WHERE id=?",
        (status or ("failed" if error else "success"), error, _now(), job_id),
    )
    conn.commit()

def _record_event(conn: sqlite3.Connection, integration_id: int, external_id: str, event_type: str,
                  payload: dict[str, Any], occurred_at: str) -> bool:
    try:
        conn.execute(
            "INSERT INTO external_events(integration_id,external_id,event_type,payload,occurred_at,created_at) VALUES (?,?,?,?,?,?)",
            (integration_id, external_id[:255], event_type, json.dumps(payload, ensure_ascii=False), occurred_at, _now()),
        )
        return True
    except sqlite3.IntegrityError:
        return False

def _ensure_contribution(
    conn: sqlite3.Connection,
    project_id: int,
    user_id: int,
    payload: dict[str, Any],
    *,
    source: str = "github",
    kind: str = "code",
) -> bool:
    stamp = _now()
    cur = conn.execute(
        """INSERT INTO contributions(project_id,user_id,kind,title,description,quantity,metadata,evidence_url,status,source,occurred_at,created_at,updated_at,created_by)
        VALUES (?,?,?,?,?,?,?,?,'pending',?,?,?,?,?)""",
        (
            project_id, user_id, kind, payload["title"][:200], payload["description"], 1,
            json.dumps({source: payload.get("meta", {})}, ensure_ascii=False), payload.get("evidence_url"), source,
            payload.get("occurred_at") or stamp, stamp, stamp, user_id,
        ),
    )
    return cur.lastrowid is not None

def _sync_repo(
    conn: sqlite3.Connection,
    *,
    integration_id: int,
    token: str,
    repo: str,
    project_id: int,
    user_map: dict[str, int],
    since: Optional[str] = None,
) -> dict[str, int]:
    stats = {"created": 0, "skipped": 0, "commits": 0, "pull_requests": 0, "issues": 0, "reviews": 0, "diff_details": 0}
    diff_details = 0
    commit_params: dict[str, Any] = {"per_page": 100}
    if since:
        commit_params["since"] = since
    commits = _github_get_items(f"{GITHUB_API}/repos/{repo}/commits", token, commit_params)
    for item in commits:
        gh_login = (item.get("author") or {}).get("login")
        email = ((item.get("commit") or {}).get("author", {}) or {}).get("email", "")
        actor = gh_login or email
        target = user_map.get(actor or "") or user_map.get(email)
        sha = item.get("sha")
        if not sha:
            stats["skipped"] += 1
            continue
        message = ((item.get("commit") or {}).get("message") or "").splitlines()
        first_line = message[0][:60] if message else ""
        occurred = ((item.get("commit") or {}).get("author", {}) or {}).get("date", "") or _now()
        evidence = item.get("html_url") or f"https://github.com/{repo}/commit/{sha}"
        external_id = f"commit:{repo}:{sha}"
        if not _record_event(conn, integration_id, external_id, "commit", {"sha": sha, "repo": repo, "actor": actor}, occurred):
            stats["skipped"] += 1
            continue
        stats["commits"] += 1
        if target is None:
            continue
        diff: Optional[tuple[int, int]] = None
        if diff_details < DIFF_DETAIL_LIMIT:
            diff = _commit_diff_stats(token, repo, sha)
            if diff is not None:
                diff_details += 1
                stats["diff_details"] += 1
        meta: dict[str, Any] = {"external_id": external_id, "sha": sha, "repo": repo, "author": actor}
        if diff is not None:
            meta["additions"] = diff[0]
            meta["deletions"] = diff[1]
        if _ensure_contribution(conn, project_id, target, {
            "title": f"提交：{repo.split('/')[-1]}#{sha[:7]} {first_line}",
            "description": f"由 GitHub 自动同步 · {repo} · {occurred}",
            "evidence_url": evidence, "occurred_at": occurred,
            "meta": meta,
        }):
            stats["created"] += 1

    pulls = _github_get_items(f"{GITHUB_API}/repos/{repo}/pulls", token, {"state": "all", "per_page": 100})
    for item in pulls:
        author = (item.get("user") or {}).get("login", "")
        merged_by = (item.get("merged_by") or {}).get("login", "")
        actor = author or merged_by
        target = user_map.get(author) or user_map.get(merged_by)
        number = item.get("number")
        if not number:
            stats["skipped"] += 1
            continue
        title = (item.get("title") or "")[:60]
        occurred = item.get("merged_at") or item.get("created_at") or _now()
        evidence = item.get("html_url") or f"https://github.com/{repo}/pull/{number}"
        external_id = f"pull:{repo}:{number}"
        if not _record_event(conn, integration_id, external_id, "pull_request", {"number": number, "repo": repo, "actor": actor}, occurred):
            stats["skipped"] += 1
            continue
        stats["pull_requests"] += 1
        if target is not None and _ensure_contribution(conn, project_id, target, {
            "title": f"PR：{repo.split('/')[-1]}#{number} {title}",
            "description": f"由 GitHub 自动同步 · {repo} · {occurred}",
            "evidence_url": evidence, "occurred_at": occurred,
            "meta": {"external_id": external_id, "number": number, "repo": repo, "author": author, "merged_by": merged_by},
        }):
            stats["created"] += 1

    issue_params: dict[str, Any] = {"state": "all", "per_page": 100}
    if since:
        issue_params["since"] = since
    issues = _github_get_items(f"{GITHUB_API}/repos/{repo}/issues", token, issue_params)
    for item in issues:
        if item.get("pull_request"):
            continue
        author = (item.get("user") or {}).get("login", "")
        target = user_map.get(author)
        number = item.get("number")
        if not number:
            stats["skipped"] += 1
            continue
        occurred = item.get("created_at") or _now()
        evidence = item.get("html_url") or f"https://github.com/{repo}/issues/{number}"
        external_id = f"issue:{repo}:{number}"
        if not _record_event(conn, integration_id, external_id, "issue", {"number": number, "repo": repo, "actor": author}, occurred):
            stats["skipped"] += 1
            continue
        stats["issues"] += 1
        if target is not None and _ensure_contribution(conn, project_id, target, {
            "title": f"Issue：{repo.split('/')[-1]}#{number} {(item.get('title') or '')[:60]}",
            "description": f"由 GitHub 自动同步 · {repo} · {occurred}",
            "evidence_url": evidence, "occurred_at": occurred,
            "meta": {"external_id": external_id, "number": number, "repo": repo, "author": author},
        }):
            stats["created"] += 1

    for pull in pulls:
        number = pull.get("number")
        if not number:
            continue
        reviews = _github_get_items(f"{GITHUB_API}/repos/{repo}/pulls/{number}/reviews", token, {"per_page": 100})
        for review in reviews:
            reviewer = (review.get("user") or {}).get("login", "")
            target = user_map.get(reviewer)
            review_id = review.get("id")
            if not review_id:
                stats["skipped"] += 1
                continue
            occurred = review.get("submitted_at") or _now()
            evidence = review.get("html_url") or pull.get("html_url")
            external_id = f"review:{repo}:{review_id}"
            if not _record_event(conn, integration_id, external_id, "pull_request_review", {"review_id": review_id, "pull_number": number, "repo": repo, "actor": reviewer}, occurred):
                stats["skipped"] += 1
                continue
            stats["reviews"] += 1
            if target is not None and _ensure_contribution(conn, project_id, target, {
                "title": f"Review：{repo.split('/')[-1]}#{number} {(review.get('state') or 'review').lower()}",
                "description": f"由 GitHub 自动同步 · {repo} · {occurred}",
                "evidence_url": evidence, "occurred_at": occurred,
                "meta": {"external_id": external_id, "review_id": review_id, "pull_number": number, "repo": repo, "reviewer": reviewer, "state": review.get("state")},
            }):
                stats["created"] += 1
    return stats

# ---------- OAuth ----------
@router.get("/api/integrations/github/auth-url")
def github_auth_url(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request); conn.close()
    cfg = _github_config()
    try:
        _fernet()
    except RuntimeError as exc:
        return {"configured": False, "message": str(exc)}
    if not cfg["client_id"] or not cfg["client_secret"]:
        return {"configured": False, "message": "未配置 GitHub 接入：请在 .env 填写 GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET"}
    state = secrets.token_urlsafe(24)
    _store_state(user["id"], state, cfg["redirect_uri"] or "", _session_hash(request))
    params = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "scope": "repo", "state": state, "allow_signup": "true"}
    return {"configured": True, "url": f"{GITHUB_OAUTH}/authorize?{urlencode(params)}", "state": state}

@router.get("/api/integrations/github/callback")
def github_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None) -> Any:
    def _back(err: str) -> RedirectResponse:
        return RedirectResponse(f"{FRONTEND_BASE}/#/github?error={err}")
    if error: return _back(error)
    if not code or not state: return _back("missing_code_or_state")
    conn = db()
    try:
        user = require_user(conn, request)
    except Exception:
        conn.close(); return _back("login_required")
    conn.close()
    state_row = _consume_state(state, user["id"], _session_hash(request))
    if not state_row:
        return _back("invalid_state")
    try:
        identity = _exchange_github_identity(code, state_row.get("redirect_uri") or (_github_config()["redirect_uri"] or ""))
        conn = db()
        _upsert_connection(conn, int(user["id"]), "github", identity)
        conn.close()
    except (AdapterError, RuntimeError):
        return _back("github_api_error")
    return RedirectResponse(f"{FRONTEND_BASE}/#/github?connected={identity.external_username}")

@router.post("/api/integrations/github/disconnect")
def github_disconnect(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request)
    row = _connection(conn, user["id"])
    if row:
        conn.execute("UPDATE project_integrations SET enabled=0,updated_at=? WHERE connection_id=?", (_now(), row["id"]))
        conn.execute("UPDATE platform_connections SET status='revoked',credentials_ref=NULL,updated_at=? WHERE id=?", (_now(), row["id"]))
        conn.commit()
    conn.close()
    return {"disconnected": True}

@router.get("/api/integrations/github/status")
def github_status(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request); row = _connection(conn, user["id"])
    out: dict[str, Any] = {"configured": bool(_github_config()["client_id"]), "connected": False, "account": None, "projects": []}
    if row:
        out["connected"] = True
        out["account"] = row["external_username"] or row["external_account_id"]
        out["projects"] = [
            {"project_id": r["project_id"], "integration_id": r["id"], "config": _config_dict(r["config"]), "enabled": bool(r["enabled"])}
            for r in conn.execute("SELECT * FROM project_integrations WHERE connection_id=? AND platform='github'", (row["id"],)).fetchall()
        ]
    conn.close()
    return out

@router.post("/api/projects/{project_id}/integrations/github/sync")
def github_sync(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """按项目 owner 权限执行 GitHub 同步，并保留跨仓库部分成功结果。"""
    conn = db()
    project, user, role = ensure_project_access(conn, project_id, request, "owner")
    ensure_writable(project)
    connection = _connection(conn, user["id"])
    if not connection:
        conn.close()
        fail(400, "BAD_REQUEST", "尚未连接 GitHub，请先完成 OAuth 授权")
    integration = _integration(conn, project_id, connection["id"])
    config = _config_dict(payload.get("config") or (integration["config"] if integration else None))
    repos = []
    for raw_repo in config.get("repos") or []:
        repo = str(raw_repo).strip()
        if repo and repo not in repos:
            repos.append(repo)
    if not repos:
        conn.close()
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "repos", "message": "至少配置一个仓库（owner/name）"}])
    if not integration:
        cur = conn.execute(
            "INSERT INTO project_integrations(project_id,connection_id,platform,config,enabled,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
            (project_id, connection["id"], "github", json.dumps(config, ensure_ascii=False), _now(), _now()),
        )
        integration_id = int(cur.lastrowid)
    else:
        integration_id = int(integration["id"])
        conn.execute(
            "UPDATE project_integrations SET config=?, enabled=1, updated_at=? WHERE id=?",
            (json.dumps(config, ensure_ascii=False), _now(), integration_id),
        )
    conn.commit()
    job_id = _insert_job(conn, integration_id)
    token = _decrypt(connection["credentials_ref"])
    if not token:
        _finish_job(conn, job_id, error="token 不可用，请重新连接")
        conn.close()
        fail(400, "BAD_REQUEST", "token 不可用，请重新连接 GitHub")
    members = conn.execute(
        "SELECT u.id,u.name,u.email FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND m.status='active'",
        (project_id,),
    ).fetchall()
    logins_override = config.get("logins") or {}
    user_map: dict[str, int] = {}
    for member in members:
        if member["email"]:
            user_map[member["email"]] = int(member["id"])
    connected_members = conn.execute(
        "SELECT pc.external_username,pc.external_account_id,pc.user_id FROM platform_connections pc JOIN memberships m ON m.user_id=pc.user_id WHERE pc.platform='github' AND pc.status='active' AND m.project_id=? AND m.status='active'",
        (project_id,),
    ).fetchall()
    for connected_member in connected_members:
        if connected_member["external_username"]:
            user_map[str(connected_member["external_username"])] = int(connected_member["user_id"])
        if connected_member["external_account_id"]:
            user_map[str(connected_member["external_account_id"])] = int(connected_member["user_id"])
    if isinstance(logins_override, dict):
        for key, uid in logins_override.items():
            try:
                user_map[str(key)] = int(uid)
            except (TypeError, ValueError):
                continue
    user_map.setdefault(str(connection["external_username"] or connection["external_account_id"]), int(user["id"]))
    created = skipped = 0
    event_statistics = {"commits": 0, "pull_requests": 0, "issues": 0, "reviews": 0}
    diff_detail_total = 0
    errors: list[str] = []
    succeeded_repos = 0
    for repo in repos:
        try:
            stats = _sync_repo(
                conn,
                integration_id=integration_id,
                token=token,
                repo=repo,
                project_id=project_id,
                user_map=user_map,
                since=str(payload.get("since") or config.get("sync_from") or config.get("last_synced_at") or "") or None,
            )
            conn.commit()
            created += stats["created"]
            skipped += stats["skipped"]
            diff_detail_total += stats["diff_details"]
            for key in event_statistics:
                event_statistics[key] += stats[key]
            succeeded_repos += 1
        except httpx.HTTPStatusError as exc:
            conn.rollback()
            status = exc.response.status_code if exc.response is not None else "unknown"
            errors.append(f"仓库 {repo} GitHub API 返回 HTTP {status}")
        except httpx.HTTPError:
            conn.rollback()
            errors.append(f"仓库 {repo} GitHub API 网络请求失败")
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            conn.rollback()
            errors.append(f"仓库 {repo} 数据格式或本地存储处理失败")
        except Exception:
            conn.rollback()
            errors.append(f"仓库 {repo} 同步失败")
    synced_at = _now()
    if succeeded_repos:
        config["last_synced_at"] = synced_at
        conn.execute("UPDATE project_integrations SET config=?,updated_at=? WHERE id=?", (json.dumps(config, ensure_ascii=False), synced_at, integration_id))
        conn.execute("UPDATE platform_connections SET last_synced_at=?,updated_at=? WHERE id=?", (synced_at, synced_at, connection["id"]))
        conn.commit()
    if errors:
        job_status = "partial" if (created or skipped or succeeded_repos) else "failed"
        _finish_job(conn, job_id, error="；".join(errors)[:1000], status=job_status)
    else:
        _finish_job(conn, job_id, status="success")
    conn.close()
    result = {
        "created": created,
        "skipped": skipped,
        "integration_id": integration_id,
        "job_id": job_id,
        "status": "partial" if errors and (created or skipped or succeeded_repos) else ("failed" if errors else "success"),
        "statistics": event_statistics,
        "diff_details": diff_detail_total,
        "diff_detail_limit": DIFF_DETAIL_LIMIT,
        "errors": errors,
    }
    if errors and not (created or skipped or succeeded_repos):
        fail(502, "BAD_GATEWAY", "GitHub 同步失败", [{"message": message} for message in errors])
    return result
