"""D5 外部平台接入（GitHub OAuth + 单向同步）。

只做 GitHub 一项；飞书 / 腾讯文档仅记录 TODO。复用 platform_connections / project_integrations /
external_events / sync_jobs 四张既有表，不建新表。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from backend.core.context import *
from backend.schemas import *

router = APIRouter()

GITHUB_API = "https://api.github.com"
GITHUB_OAUTH = "https://github.com/login/oauth"
STATE_TTL_SECONDS = 600
HTTP_TIMEOUT = 15.0
FRONTEND_BASE = os.getenv("COLLAB_FRONTEND_BASE", "http://127.0.0.1:5173")

_pending_states: dict[str, str] = {}

def _now() -> str: return now_iso()

# ---------- token 混淆存储（XOR + base64，服务端隔离；断开连接即删除） ----------
def _xor(raw: bytes) -> bytes:
    key = (os.getenv("GITHUB_TOKEN_SECRET") or "collab-ledger-d5-local").encode()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))

def _encrypt(token: str) -> str: return base64.b64encode(_xor(token.encode())).decode()
def _decrypt(blob: Optional[str]) -> Optional[str]:
    if not blob: return None
    try: return _xor(base64.b64decode(blob)).decode()
    except Exception: return None

def _state_expired(stamp: str) -> bool:
    try: created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError: return True
    return datetime.now(timezone.utc) - created > timedelta(seconds=STATE_TTL_SECONDS)

def _github_config() -> dict[str, Optional[str]]:
    return {
        "client_id": os.getenv("GITHUB_CLIENT_ID") or None,
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET") or None,
        "redirect_uri": os.getenv("GITHUB_REDIRECT_URI") or "http://127.0.0.1:8000/api/integrations/github/callback",
    }

def _connection(conn: sqlite3.Connection, user_id: int, platform: str = "github") -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM platform_connections WHERE user_id=? AND platform=? AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id, platform),
    ).fetchone()

def _integration(conn: sqlite3.Connection, project_id: int, connection_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM project_integrations WHERE project_id=? AND connection_id=? AND platform='github' LIMIT 1",
        (project_id, connection_id),
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


def _insert_job(conn: sqlite3.Connection, integration_id: int) -> int:
    stamp = _now()
    cur = conn.execute(
        "INSERT INTO sync_jobs(integration_id,status,started_at,created_at) VALUES (?, 'running', ?, ?)",
        (integration_id, stamp, stamp),
    )
    conn.commit()
    return int(cur.lastrowid)

def _finish_job(conn: sqlite3.Connection, job_id: int, *, error: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE sync_jobs SET status=?, error=?, finished_at=? WHERE id=?",
        ("failed" if error else "success", error, _now(), job_id),
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

def _ensure_contribution(conn: sqlite3.Connection, project_id: int, user_id: int, p: dict[str, Any]) -> bool:
    stamp = _now()
    cur = conn.execute(
        """INSERT INTO contributions(project_id,user_id,kind,title,description,quantity,metadata,evidence_url,status,source,occurred_at,created_at,updated_at,created_by)
        VALUES (?,?,?,?,?,?,?,?,'pending','github',?,?,?,?)""",
        (project_id, user_id, "code", p["title"][:200], p["description"], 1,
         json.dumps({"github": p.get("meta", {})}, ensure_ascii=False), p["evidence_url"],
         p.get("occurred_at") or stamp, stamp, stamp, user_id),
    )
    return cur.lastrowid is not None

def _sync_repo(conn: sqlite3.Connection, *, integration_id: int, token: str, repo: str,
               project_id: int, user_map: dict[str, int]) -> dict[str, int]:
    created = skipped = 0
    resp = _github_get(f"{GITHUB_API}/repos/{repo}/commits", token, {"per_page": 50})
    resp.raise_for_status()
    for item in resp.json():
        gh_login = (item.get("author") or {}).get("login")
        email = ((item.get("commit") or {}).get("author", {}) or {}).get("email", "")
        target = user_map.get(gh_login or "") or user_map.get(email)
        if target is None: continue
        sha = item["sha"]
        message = ((item.get("commit") or {}).get("message") or "").splitlines()
        first_line = message[0][:60] if message else ""
        occurred = ((item.get("commit") or {}).get("author", {}) or {}).get("date", "") or _now()
        evidence = item.get("html_url") or f"https://github.com/{repo}/commit/{sha}"
        if not _record_event(conn, integration_id, f"commit:{repo}:{sha}", "commit", {"sha": sha, "repo": repo}, occurred):
            skipped += 1; continue
        if _ensure_contribution(conn, project_id, target, {
            "kind": "code", "title": f"提交：{repo.split('/')[-1]}#{sha[:7]} {first_line}",
            "description": f"由 GitHub 自动同步 · {repo} · {occurred}",
            "evidence_url": evidence, "occurred_at": occurred,
            "meta": {"sha": sha, "repo": repo, "author": gh_login or email},
        }): created += 1
        else: skipped += 1
    resp = _github_get(f"{GITHUB_API}/repos/{repo}/pulls", token, {"state": "all", "per_page": 50})
    resp.raise_for_status()
    for item in resp.json():
        author = (item.get("user") or {}).get("login", "")
        merged_by = (item.get("merged_by") or {}).get("login", "")
        target = user_map.get(author) or user_map.get(merged_by)
        if target is None: continue
        number = item["number"]
        title = (item.get("title") or "")[:60]
        occurred = item.get("merged_at") or item.get("created_at") or _now()
        evidence = item.get("html_url") or f"https://github.com/{repo}/pull/{number}"
        if not _record_event(conn, integration_id, f"pull:{repo}:{number}", "pull_request", {"number": number, "repo": repo}, occurred):
            skipped += 1; continue
        if _ensure_contribution(conn, project_id, target, {
            "kind": "code", "title": f"PR：{repo.split('/')[-1]}#{number} {title}",
            "description": f"由 GitHub 自动同步 · {repo} · {occurred}",
            "evidence_url": evidence, "occurred_at": occurred,
            "meta": {"number": number, "repo": repo, "author": author, "merged_by": merged_by},
        }): created += 1
        else: skipped += 1
    return {"created": created, "skipped": skipped}

# ---------- OAuth ----------
@router.get("/api/integrations/github/auth-url")
def github_auth_url(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request); conn.close()
    cfg = _github_config()
    if not cfg["client_id"]:
        return {"configured": False, "message": "未配置 GitHub 接入：请在 .env 填写 GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET"}
    state = secrets.token_urlsafe(24)
    _pending_states[state] = _now()
    params = {"client_id": cfg["client_id"], "redirect_uri": cfg["redirect_uri"], "scope": "repo", "state": state, "allow_signup": "true"}
    return {"configured": True, "url": f"{GITHUB_OAUTH}/authorize?{urlencode(params)}", "state": state}

@router.get("/api/integrations/github/callback")
def github_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None) -> Any:
    def _back(err: str) -> RedirectResponse:
        return RedirectResponse(f"{FRONTEND_BASE}/#/github?error={err}")
    if error: return _back(error)
    if not code or not state: return _back("missing_code_or_state")
    stamp = _pending_states.pop(state, None)
    if stamp is None or _state_expired(stamp): return _back("invalid_state")
    cfg = _github_config()
    if not cfg["client_id"] or not cfg["client_secret"]: return _back("not_configured")
    try:
        token_resp = httpx.post(f"{GITHUB_OAUTH}/access_token", json={
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code": code, "redirect_uri": cfg["redirect_uri"],
        }, headers={"Accept": "application/json"}, timeout=HTTP_TIMEOUT)
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token: return _back("token_exchange_failed")
        profile = _github_get(f"{GITHUB_API}/user", access_token)
        profile.raise_for_status()
        gh_user = profile.json()
    except httpx.HTTPError:
        return _back("github_api_error")
    conn = db()
    user = require_user(conn, request)
    existing = _connection(conn, user["id"])
    stamp = _now()
    if existing:
        conn.execute("UPDATE platform_connections SET external_account_id=?, credentials_ref=?, status='active', updated_at=? WHERE id=?",
                     (str(gh_user["id"]), _encrypt(access_token), stamp, existing["id"]))
        conn_id = int(existing["id"])
    else:
        cur = conn.execute(
            "INSERT INTO platform_connections(user_id,platform,external_account_id,credentials_ref,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (user["id"], "github", str(gh_user["id"]), _encrypt(access_token), "active", stamp, stamp),
        )
        conn_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return RedirectResponse(f"{FRONTEND_BASE}/#/github?connected={gh_user.get('login', '')}")

@router.post("/api/integrations/github/disconnect")
def github_disconnect(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request)
    row = _connection(conn, user["id"])
    if row:
        conn.execute("DELETE FROM project_integrations WHERE connection_id=?", (row["id"],))
        conn.execute("DELETE FROM platform_connections WHERE id=?", (row["id"],))
        conn.commit()
    conn.close()
    return {"disconnected": True}

@router.get("/api/integrations/github/status")
def github_status(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request); row = _connection(conn, user["id"])
    out: dict[str, Any] = {"configured": bool(_github_config()["client_id"]), "connected": False, "account": None, "projects": []}
    if row:
        out["connected"] = True
        out["account"] = row["external_account_id"]
        out["projects"] = [
            {"project_id": r["project_id"], "integration_id": r["id"], "config": _config_dict(r["config"]), "enabled": bool(r["enabled"])}
            for r in conn.execute("SELECT * FROM project_integrations WHERE connection_id=? AND platform='github'", (row["id"],)).fetchall()
        ]
    conn.close()
    return out

@router.post("/api/projects/{project_id}/integrations/github/sync")
def github_sync(project_id: int, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    conn = db(); project, user, role = ensure_project_access(conn, project_id, request, "member")
    ensure_writable(project)
    connection = _connection(conn, user["id"])
    if not connection:
        conn.close(); fail(400, "BAD_REQUEST", "尚未连接 GitHub，请先完成 OAuth 授权")
    integration = _integration(conn, project_id, connection["id"])
    config = _config_dict(payload.get("config") or (integration["config"] if integration else None))
    repos = [r.strip() for r in (config.get("repos") or []) if r and r.strip()]
    if not repos:
        conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "repos", "message": "至少配置一个仓库（owner/name）"}])
    if not integration:
        cur = conn.execute(
            "INSERT INTO project_integrations(project_id,connection_id,platform,config,enabled,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
            (project_id, connection["id"], "github", json.dumps(config, ensure_ascii=False), _now(), _now()),
        )
        integration_id = int(cur.lastrowid)
    else:
        integration_id = int(integration["id"])
        conn.execute("UPDATE project_integrations SET config=?, updated_at=? WHERE id=?",
                     (json.dumps(config, ensure_ascii=False), _now(), integration_id))
    conn.commit()
    job_id = _insert_job(conn, integration_id)
    token = _decrypt(connection["credentials_ref"])
    if not token:
        _finish_job(conn, job_id, error="token 不可用，请重新连接")
        conn.close(); fail(400, "BAD_REQUEST", "token 不可用，请重新连接 GitHub")
    members = conn.execute(
        """SELECT u.id,u.name,u.email FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=?""",
        (project_id,),
    ).fetchall()
    # GitHub login 映射：以 external_account_id 为主，也可在 project_integrations.config.logins 中手动补充 {user_id: login}。
    logins_override = config.get("logins") or {}
    user_map: dict[str, int] = {}
    for m in members:
        if m["email"]: user_map[m["email"]] = m["id"]
    for key, uid in (logins_override.items() if isinstance(logins_override, dict) else []):
        try: user_map[str(key)] = int(uid)
        except (TypeError, ValueError): continue
    # 当前用户自己的 GitHub id 映射
    user_map.setdefault(str(connection["external_account_id"]), user["id"])
    created = skipped = 0
    errors: list[str] = []
    try:
        for repo in repos:
            stats = _sync_repo(conn, integration_id=integration_id, token=token, repo=repo, project_id=project_id, user_map=user_map)
            created += stats["created"]; skipped += stats["skipped"]
    except httpx.HTTPError as exc:
        errors.append(f"GitHub API 调用失败：{exc}")
    except Exception as exc:  # 数据库或解析错误
        errors.append(f"同步失败：{exc}")
    finally:
        if errors:
            conn.rollback()
            _finish_job(conn, job_id, error="; ".join(errors))
        else:
            conn.commit()
            _finish_job(conn, job_id)
    conn.close()
    if errors:
        fail(502, "BAD_GATEWAY", "GitHub 同步失败", [{"message": e} for e in errors])
    return {"created": created, "skipped": skipped, "integration_id": integration_id, "job_id": job_id}
