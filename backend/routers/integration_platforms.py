"""D5 通用平台、GitHub 契约别名、Webhook 与反向写入路由。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import RedirectResponse, Response

from backend.core.context import *
from backend.schemas import *
from backend.services.platform_adapters import ADAPTERS, AdapterError, PlatformIdentity
from backend.routers.integrations import (
    FRONTEND_BASE, GITHUB_API, GITHUB_OAUTH,
    _config_dict, _connection, _consume_state, _decrypt, _ensure_contribution,
    _exchange_github_identity, _fernet, _finish_job, _github_config, _github_post,
    _insert_job, _integration, _integration_public, _now, _record_event,
    _safe_external_error, _session_hash, _store_state, _upsert_connection, github_callback,
    github_status, github_sync,
)

router = APIRouter()

PLATFORM_CATALOG = [
    {"platform": "github", "name": "GitHub", "category": "code", "oauth_supported": True, "scopes": ["repo", "read:org"], "enabled": True},
    {"platform": "feishu", "name": "飞书", "category": "document", "oauth_supported": True, "scopes": ["wiki:read", "docx:read"], "enabled": False},
    {"platform": "tencent_doc", "name": "腾讯文档", "category": "document", "oauth_supported": False, "scopes": ["document:read"], "enabled": False},
]
SUPPORTED_PLATFORMS = {item["platform"] for item in PLATFORM_CATALOG}

# ---------- 通用平台契约 ----------
@router.get("/api/integrations/platforms")
def list_platforms(request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request); conn.close()
    items = []
    for item in PLATFORM_CATALOG:
        current = dict(item)
        if current["platform"] == "github":
            current["enabled"] = bool(_github_config()["client_id"] and _github_config()["client_secret"])
        elif current["platform"] in ADAPTERS:
            current["enabled"] = ADAPTERS[current["platform"]].configured()
        items.append(current)
    return {"items": items}


@router.get("/api/integrations/connections")
def list_connections(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request)
    rows = conn.execute(
        "SELECT * FROM platform_connections WHERE user_id=? ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    items = []
    for row in rows:
        try:
            scopes = json.loads(row["scopes"] or "[]")
        except (json.JSONDecodeError, TypeError):
            scopes = []
        items.append({
            "id": int(row["id"]),
            "platform": row["platform"],
            "external_account_id": row["external_account_id"],
            "external_username": row["external_username"],
            "status": row["status"],
            "connected_at": row["connected_at"] or row["created_at"],
            "last_synced_at": row["last_synced_at"],
            "scopes": scopes,
        })
    conn.close()
    return {"items": items}


@router.post("/api/integrations/{platform}/oauth/start")
def platform_oauth_start(platform: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    if platform not in SUPPORTED_PLATFORMS:
        fail(404, "NOT_FOUND", "不支持的外部平台")
    conn = db(); user = require_user(conn, request); conn.close()
    state = secrets.token_urlsafe(24)
    if platform == "github":
        cfg = _github_config()
        redirect_uri = str(payload.get("redirect_uri") or cfg["redirect_uri"] or "")
        if not cfg["client_id"] or not cfg["client_secret"]:
            fail(409, "NOT_CONFIGURED", "GitHub OAuth 未配置")
        authorize_url = f"{GITHUB_OAUTH}/authorize?{urlencode({'client_id': cfg['client_id'], 'redirect_uri': redirect_uri, 'scope': 'repo', 'state': state, 'allow_signup': 'true'})}"
    else:
        adapter = ADAPTERS.get(platform)
        if not adapter:
            fail(409, "NOT_CONFIGURED", "该平台当前不支持 OAuth")
        redirect_uri = str(payload.get("redirect_uri") or os.getenv(f"{platform.upper()}_REDIRECT_URI") or "")
        if not redirect_uri:
            fail(422, "VALIDATION_ERROR", "redirect_uri 不能为空")
        try:
            authorize_url = adapter.oauth_start(state, redirect_uri)
        except AdapterError as exc:
            fail(409, "NOT_CONFIGURED", str(exc))
    _store_state(int(user["id"]), state, redirect_uri, _session_hash(request), platform)
    return {"authorize_url": authorize_url, "state": state, "configured": True}


@router.post("/api/integrations/{platform}/connections", status_code=201)
def create_platform_connection(platform: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    if platform not in SUPPORTED_PLATFORMS:
        fail(404, "NOT_FOUND", "不支持的外部平台")
    conn = db(); user = require_user(conn, request); conn.close()
    try:
        if platform == "tencent_doc":
            token = str(payload.get("access_token") or "").strip()
            account_id = str(payload.get("external_account_id") or "").strip()
            if not token or not account_id:
                fail(422, "VALIDATION_ERROR", "腾讯文档连接需要 access_token 与 external_account_id")
            identity = PlatformIdentity(account_id, str(payload.get("external_username") or account_id), token, ["document:read"])
        else:
            code = str(payload.get("code") or "").strip()
            state = str(payload.get("state") or "").strip()
            if not code or not state:
                fail(422, "VALIDATION_ERROR", "code 与 state 不能为空")
            state_row = _consume_state(state, int(user["id"]), _session_hash(request), platform)
            if not state_row:
                fail(400, "INVALID_STATE", "OAuth state 无效、已过期或已使用")
            redirect_uri = str(state_row.get("redirect_uri") or "")
            identity = _exchange_github_identity(code, redirect_uri) if platform == "github" else ADAPTERS[platform].exchange_code(code, redirect_uri)
        conn = db()
        connection_id = _upsert_connection(conn, int(user["id"]), platform, identity)
        conn.close()
    except (AdapterError, RuntimeError) as exc:
        fail(502, "BAD_GATEWAY", str(exc))
    return {
        "id": connection_id,
        "platform": platform,
        "external_account_id": identity.external_account_id,
        "external_username": identity.external_username,
        "status": "active",
        "scopes": identity.scopes,
    }


@router.delete("/api/integrations/connections/{connection_id}", status_code=204)
def delete_platform_connection(connection_id: int, request: Request) -> Response:
    conn = db(); user = require_user(conn, request)
    row = conn.execute("SELECT * FROM platform_connections WHERE id=? AND user_id=?", (connection_id, user["id"])).fetchone()
    if not row:
        conn.close(); fail(404, "NOT_FOUND", "平台连接不存在")
    stamp = _now()
    conn.execute("UPDATE project_integrations SET enabled=0,updated_at=? WHERE connection_id=?", (stamp, connection_id))
    conn.execute("UPDATE platform_connections SET status='revoked',credentials_ref=NULL,updated_at=? WHERE id=?", (stamp, connection_id))
    conn.commit(); conn.close()
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/integrations")
def list_project_integrations(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    rows = conn.execute("SELECT * FROM project_integrations WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    result = [_integration_public(row) for row in rows]
    conn.close()
    return {"items": result}


@router.post("/api/projects/{project_id}/integrations", status_code=201)
def create_project_integration(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    conn = db(); project, user, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    platform = str(payload.get("platform") or "").strip()
    if platform not in SUPPORTED_PLATFORMS:
        conn.close(); fail(422, "VALIDATION_ERROR", "platform 不受支持")
    connection = _connection(conn, int(user["id"]), platform)
    if not connection:
        conn.close(); fail(409, "NOT_CONNECTED", f"当前用户尚未连接 {platform}")
    config = {
        "resource_type": str(payload.get("resource_type") or ("repository" if platform == "github" else "document")),
        "resource_id": str(payload.get("resource_id") or "").strip(),
        "resource_url": payload.get("resource_url"),
        "sync_from": payload.get("sync_from"),
    }
    if platform == "github":
        repo = config["resource_id"]
        if not repo and config["resource_url"]:
            repo = str(config["resource_url"]).rstrip("/").split("github.com/")[-1].removesuffix(".git")
        if not repo:
            conn.close(); fail(422, "VALIDATION_ERROR", "GitHub 集成必须提供仓库")
        config["resource_id"] = repo
        config["repos"] = [repo]
    else:
        if not config["resource_id"]:
            conn.close(); fail(422, "VALIDATION_ERROR", "resource_id 不能为空")
        if payload.get("api_path"):
            config["api_path"] = str(payload["api_path"])
        if payload.get("actor_user_id"):
            ensure_member(conn, project_id, int(payload["actor_user_id"]))
            config["actor_user_id"] = int(payload["actor_user_id"])
    existing = _integration(conn, project_id, int(connection["id"]), platform)
    stamp = _now()
    if existing:
        conn.execute("UPDATE project_integrations SET config=?,enabled=1,updated_at=? WHERE id=?", (json.dumps(config, ensure_ascii=False), stamp, existing["id"]))
        integration_id = int(existing["id"])
    else:
        cur = conn.execute(
            "INSERT INTO project_integrations(project_id,connection_id,platform,config,enabled,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
            (project_id, connection["id"], platform, json.dumps(config, ensure_ascii=False), stamp, stamp),
        )
        integration_id = int(cur.lastrowid)
    conn.commit()
    row = conn.execute("SELECT * FROM project_integrations WHERE id=?", (integration_id,)).fetchone()
    result = _integration_public(row)
    conn.close()
    return result


def _sync_document_integration(conn: sqlite3.Connection, integration: sqlite3.Row, project: sqlite3.Row) -> dict[str, Any]:
    platform = str(integration["platform"])
    adapter = ADAPTERS.get(platform)
    if not adapter:
        raise AdapterError("当前平台没有可用适配器")
    connection = conn.execute("SELECT * FROM platform_connections WHERE id=? AND status='active'", (integration["connection_id"],)).fetchone()
    if not connection:
        raise AdapterError("平台连接已断开")
    token = _decrypt(connection["credentials_ref"])
    if not token:
        raise AdapterError("平台凭据不可用，请重新连接")
    config = _config_dict(integration["config"])
    events = adapter.fetch_events(token, config)
    target_user_id = int(config.get("actor_user_id") or project["owner_id"])
    created = skipped = 0
    for event in events:
        if not _record_event(conn, int(integration["id"]), event.external_id, event.event_type, event.payload, event.occurred_at or _now()):
            skipped += 1
            continue
        if _ensure_contribution(conn, int(project["id"]), target_user_id, {
            "title": event.title,
            "description": event.description,
            "evidence_url": event.evidence_url,
            "occurred_at": event.occurred_at,
            "meta": {"external_id": event.external_id, "actor": event.actor, **event.payload},
        }, source=platform, kind="document"):
            created += 1
    config["last_synced_at"] = _now()
    conn.execute("UPDATE project_integrations SET config=?,updated_at=? WHERE id=?", (json.dumps(config, ensure_ascii=False), config["last_synced_at"], integration["id"]))
    conn.execute("UPDATE platform_connections SET last_synced_at=?,updated_at=? WHERE id=?", (config["last_synced_at"], config["last_synced_at"], connection["id"]))
    return {"created": created, "skipped": skipped, "events": len(events), "synced_at": config["last_synced_at"]}


@router.post("/api/projects/{project_id}/integrations/{integration_id}/sync")
def sync_project_integration(project_id: int, integration_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    integration = conn.execute("SELECT * FROM project_integrations WHERE id=? AND project_id=? AND enabled=1", (integration_id, project_id)).fetchone()
    if not integration:
        conn.close(); fail(404, "NOT_FOUND", "项目平台集成不存在或已停用")
    if integration["platform"] == "github":
        config = _config_dict(integration["config"])
        conn.close()
        return github_sync(project_id, {"config": config}, request)
    job_id = _insert_job(conn, integration_id)
    try:
        result = _sync_document_integration(conn, integration, project)
        conn.commit(); _finish_job(conn, job_id, status="success")
    except AdapterError as exc:
        conn.rollback(); _finish_job(conn, job_id, error=str(exc)[:1000], status="failed")
        conn.close(); fail(502, "BAD_GATEWAY", str(exc))
    except (httpx.HTTPError, sqlite3.Error, KeyError, TypeError, ValueError):
        conn.rollback(); _finish_job(conn, job_id, error="外部平台同步失败", status="failed")
        conn.close(); fail(502, "BAD_GATEWAY", "外部平台同步失败")
    conn.close()
    return {"job_id": job_id, "status": "success", **result}


@router.get("/api/projects/{project_id}/integrations/{integration_id}/events")
def list_integration_events(
    project_id: int,
    integration_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    integration = conn.execute("SELECT * FROM project_integrations WHERE id=? AND project_id=?", (integration_id, project_id)).fetchone()
    if not integration:
        conn.close(); fail(404, "NOT_FOUND", "项目平台集成不存在")
    total = conn.execute("SELECT COUNT(*) n FROM external_events WHERE integration_id=?", (integration_id,)).fetchone()["n"]
    offset, limit = pagination(page, page_size)
    rows = conn.execute("SELECT * FROM external_events WHERE integration_id=? ORDER BY occurred_at DESC,id DESC LIMIT ? OFFSET ?", (integration_id, limit, offset)).fetchall()
    items = []
    for row in rows:
        items.append({
            "id": int(row["id"]), "platform": integration["platform"], "event_type": row["event_type"],
            "external_id": row["external_id"], "occurred_at": row["occurred_at"], "metadata": _config_dict(row["payload"]),
        })
    conn.close()
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.post("/api/projects/{project_id}/integrations/{integration_id}/retry")
def retry_project_integration(project_id: int, integration_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    failed_job = conn.execute(
        "SELECT id,status FROM sync_jobs WHERE integration_id=? AND status IN ('failed','partial') ORDER BY id DESC LIMIT 1",
        (integration_id,),
    ).fetchone()
    conn.close()
    if not failed_job:
        fail(409, "CONFLICT", "当前没有可重试的同步任务")
    return sync_project_integration(project_id, integration_id, {}, request)


# ---------- GitHub 契约别名、统计、反向写入与 Webhook ----------
@router.get("/api/github/status")
def github_status_contract(request: Request) -> dict[str, Any]:
    status = github_status(request)
    return {"connected": status["connected"], "github_username": status["account"], "connected_at": None, **status}


@router.post("/api/github/oauth/start")
def github_oauth_start_contract(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return platform_oauth_start("github", payload, request)


@router.get("/api/github/oauth/callback")
def github_oauth_callback_contract(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None) -> Any:
    return github_callback(request, code, state, error)


@router.post("/api/github/connections")
def github_connection_contract(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return create_platform_connection("github", payload, request)


@router.delete("/api/github/connections/current", status_code=204)
def github_disconnect_contract(request: Request) -> Response:
    conn = db(); user = require_user(conn, request); row = _connection(conn, int(user["id"]), "github"); conn.close()
    if not row:
        return Response(status_code=204)
    return delete_platform_connection(int(row["id"]), request)


@router.post("/api/projects/{project_id}/github/repositories", status_code=201)
def bind_github_repository(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    repository_url = str(payload.get("repository_url") or "").strip()
    repo = repository_url.rstrip("/").split("github.com/")[-1].removesuffix(".git") if repository_url else str(payload.get("repository") or "").strip()
    return create_project_integration(project_id, {
        "platform": "github", "resource_type": "repository", "resource_id": repo,
        "resource_url": repository_url or f"https://github.com/{repo}", "sync_from": payload.get("sync_from"),
        "default_branch": payload.get("default_branch") or "main",
    }, request)


@router.post("/api/projects/{project_id}/github/sync")
def github_sync_contract(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    integration_id = payload.get("repository_id")
    if integration_id:
        integration = conn.execute("SELECT * FROM project_integrations WHERE id=? AND project_id=? AND platform='github'", (int(integration_id), project_id)).fetchone()
    else:
        integration = conn.execute("SELECT * FROM project_integrations WHERE project_id=? AND platform='github' AND enabled=1 ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
    conn.close()
    if not integration:
        fail(404, "NOT_FOUND", "尚未绑定 GitHub 仓库")
    result = github_sync(project_id, {"config": _config_dict(integration["config"])}, request)
    return {
        "repository_id": int(integration["id"]), "synced_at": _now(), "status": result["status"],
        "statistics": {
            "new_commits": result["statistics"]["commits"],
            "new_pull_requests": result["statistics"]["pull_requests"],
            "new_issues": result["statistics"]["issues"],
            "new_reviews": result["statistics"]["reviews"],
        }, **result,
    }


@router.get("/api/projects/{project_id}/github/statistics")
def github_statistics(
    project_id: int,
    request: Request,
    repository_id: Optional[int] = None,
    user_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    where = ["c.project_id=?", "c.source='github'", "c.deleted_at IS NULL"]
    args: list[Any] = [project_id]
    if user_id is not None:
        where.append("c.user_id=?"); args.append(user_id)
    if start_date:
        where.append("c.occurred_at>=?"); args.append(start_date)
    if end_date:
        where.append("c.occurred_at<=?"); args.append(end_date)
    rows = conn.execute(
        f"SELECT c.user_id,u.name,COUNT(*) total,SUM(CASE WHEN c.title LIKE '提交：%' THEN 1 ELSE 0 END) commits,SUM(CASE WHEN c.title LIKE 'PR：%' THEN 1 ELSE 0 END) pull_requests,SUM(CASE WHEN c.title LIKE 'Issue：%' THEN 1 ELSE 0 END) issues,SUM(CASE WHEN c.title LIKE 'Review：%' THEN 1 ELSE 0 END) reviews FROM contributions c JOIN users u ON u.id=c.user_id WHERE {' AND '.join(where)} GROUP BY c.user_id,u.name ORDER BY c.user_id",
        args,
    ).fetchall()
    where.append("c.title LIKE '提交：%'")
    members = []
    for row in rows:
        connection = conn.execute("SELECT external_username FROM platform_connections WHERE user_id=? AND platform='github' ORDER BY id DESC LIMIT 1", (row["user_id"],)).fetchone()
        additions = deletions = 0
        for meta_row in conn.execute(
            f"SELECT c.metadata FROM contributions c WHERE {' AND '.join(where)} AND c.user_id=?",
            (*args, row["user_id"]),
        ).fetchall():
            try:
                gh_meta = (json.loads(meta_row["metadata"] or "{}") or {}).get("github") or {}
            except (json.JSONDecodeError, TypeError):
                continue
            additions += int(gh_meta.get("additions") or 0)
            deletions += int(gh_meta.get("deletions") or 0)
        members.append({
            "user_id": int(row["user_id"]), "name": row["name"], "github_username": connection["external_username"] if connection else None,
            "commits": int(row["commits"] or 0), "additions": additions, "deletions": deletions,
            "pull_requests": int(row["pull_requests"] or 0), "reviews": int(row["reviews"] or 0), "issues": int(row["issues"] or 0),
        })
    conn.close()
    return {
        "project_id": project_id, "members": members, "repository_id": repository_id,
        "calculation": "增删行来自 GitHub commit 详情接口，仅统计同步时成功拉取详情的新 commit（单次同步上限 100 条），其余为 0",
    }


def _github_write(project_id: int, repo: str, endpoint: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    conn = db(); project, user, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    connection = _connection(conn, int(user["id"]), "github")
    if not connection:
        conn.close(); fail(409, "NOT_CONNECTED", "尚未连接 GitHub")
    integrations = conn.execute("SELECT config FROM project_integrations WHERE project_id=? AND platform='github' AND enabled=1", (project_id,)).fetchall()
    allowed_repos = {item for row in integrations for item in (_config_dict(row["config"]).get("repos") or [])}
    if repo not in allowed_repos:
        conn.close(); fail(403, "FORBIDDEN", "只能向当前项目已绑定的 GitHub 仓库写入")
    token = _decrypt(connection["credentials_ref"]); conn.close()
    if not token:
        fail(409, "NOT_CONNECTED", "GitHub 凭据不可用，请重新连接")
    try:
        response = _github_post(f"{GITHUB_API}/repos/{repo}/{endpoint}", token, payload)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        fail(502, "BAD_GATEWAY", _safe_external_error(exc.response))
    except (httpx.HTTPError, ValueError):
        fail(502, "BAD_GATEWAY", "GitHub 写入失败")
    return {"repository": repo, "number": data.get("number"), "url": data.get("html_url"), "state": data.get("state"), "title": data.get("title")}


@router.post("/api/projects/{project_id}/github/issues", status_code=201)
def create_github_issue(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    repo = str(payload.get("repository") or "").strip(); title = str(payload.get("title") or "").strip()
    if not repo or not title:
        fail(422, "VALIDATION_ERROR", "repository 与 title 不能为空")
    body = {"title": title, "body": str(payload.get("body") or "")}
    if isinstance(payload.get("labels"), list): body["labels"] = payload["labels"]
    return _github_write(project_id, repo, "issues", body, request)


@router.post("/api/projects/{project_id}/github/pulls", status_code=201)
def create_github_pull(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    required = {key: str(payload.get(key) or "").strip() for key in ("repository", "title", "head", "base")}
    if any(not value for value in required.values()):
        fail(422, "VALIDATION_ERROR", "repository、title、head、base 不能为空")
    return _github_write(project_id, required.pop("repository"), "pulls", {**required, "body": str(payload.get("body") or "")}, request)


@router.post("/api/projects/{project_id}/integrations/{integration_id}/github/webhook")
def register_github_webhook(project_id: int, integration_id: int, request: Request) -> dict[str, Any]:
    conn = db(); project, user, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    integration = conn.execute("SELECT * FROM project_integrations WHERE id=? AND project_id=? AND platform='github'", (integration_id, project_id)).fetchone()
    if not integration:
        conn.close(); fail(404, "NOT_FOUND", "GitHub 项目集成不存在")
    connection = conn.execute("SELECT * FROM platform_connections WHERE id=? AND user_id=? AND status='active'", (integration["connection_id"], user["id"])).fetchone()
    token = _decrypt(connection["credentials_ref"]) if connection else None
    config = _config_dict(integration["config"]); conn.close()
    callback_base = (os.getenv("GITHUB_WEBHOOK_BASE_URL") or "").rstrip("/")
    secret = os.getenv("GITHUB_WEBHOOK_SECRET") or ""
    if not token or not callback_base or not secret:
        fail(409, "NOT_CONFIGURED", "Webhook 需要有效 GitHub 连接、GITHUB_WEBHOOK_BASE_URL 与 GITHUB_WEBHOOK_SECRET")
    hooks = []
    for repo in config.get("repos") or []:
        try:
            response = _github_post(f"{GITHUB_API}/repos/{repo}/hooks", token, {
                "name": "web", "active": True, "events": ["push", "pull_request", "pull_request_review", "issues"],
                "config": {"url": f"{callback_base}/api/integrations/github/webhook/{integration_id}", "content_type": "json", "secret": secret, "insecure_ssl": "0"},
            })
            response.raise_for_status(); data = response.json()
            hooks.append({"repository": repo, "hook_id": data.get("id"), "url": data.get("url")})
        except httpx.HTTPStatusError as exc:
            fail(502, "BAD_GATEWAY", _safe_external_error(exc.response))
        except (httpx.HTTPError, ValueError):
            fail(502, "BAD_GATEWAY", "GitHub Webhook 注册失败")
    return {"integration_id": integration_id, "hooks": hooks, "active": True}


@router.post("/api/integrations/github/webhook/{integration_id}")
async def receive_github_webhook(
    integration_id: int,
    request: Request,
    x_hub_signature_256: Optional[str] = Header(default=None),
    x_github_event: Optional[str] = Header(default=None),
    x_github_delivery: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    secret = (os.getenv("GITHUB_WEBHOOK_SECRET") or "").encode("utf-8")
    body = await request.body()
    if not secret or not x_hub_signature_256:
        fail(401, "UNAUTHORIZED", "Webhook 签名缺失")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        fail(401, "UNAUTHORIZED", "Webhook 签名无效")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail(400, "BAD_REQUEST", "Webhook JSON 无效")
    conn = db()
    integration = conn.execute("SELECT * FROM project_integrations WHERE id=? AND platform='github' AND enabled=1", (integration_id,)).fetchone()
    if not integration:
        conn.close(); fail(404, "NOT_FOUND", "GitHub 项目集成不存在")
    config = _config_dict(integration["config"])
    repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
    if repo not in (config.get("repos") or []):
        conn.close(); fail(403, "FORBIDDEN", "Webhook 仓库未绑定到该项目")
    event_type = x_github_event or "unknown"
    delivery_id = x_github_delivery or hashlib.sha256(body).hexdigest()
    occurred_at = payload.get("timestamp") or ((payload.get("head_commit") or {}).get("timestamp")) or _now()
    created_event = _record_event(conn, integration_id, f"webhook:{delivery_id}", event_type, payload, occurred_at)
    contribution_created = False
    actor = ((payload.get("sender") or {}).get("login") or "").strip()
    member = conn.execute(
        "SELECT pc.user_id FROM platform_connections pc JOIN memberships m ON m.user_id=pc.user_id WHERE pc.platform='github' AND pc.external_username=? AND pc.status='active' AND m.project_id=? AND m.status='active' LIMIT 1",
        (actor, integration["project_id"]),
    ).fetchone() if actor else None
    if created_event and member and event_type != "ping":
        subject = payload.get("issue") or payload.get("pull_request") or payload.get("review") or payload.get("head_commit") or {}
        title = str(subject.get("title") or subject.get("message") or f"GitHub {event_type}").splitlines()[0]
        contribution_created = _ensure_contribution(conn, int(integration["project_id"]), int(member["user_id"]), {
            "title": f"GitHub {event_type}：{title}", "description": f"由 GitHub Webhook 实时同步 · {repo}",
            "evidence_url": subject.get("html_url") or subject.get("url"), "occurred_at": occurred_at,
            "meta": {"delivery_id": delivery_id, "event_type": event_type, "repo": repo, "actor": actor},
        })
    conn.commit(); conn.close()
    return {"accepted": True, "duplicate": not created_event, "contribution_created": contribution_created}
