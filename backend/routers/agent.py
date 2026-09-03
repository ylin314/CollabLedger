from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse, Response

from backend.auth import COOKIE_NAME, create_session, hash_password, iso_utc, revoke_session, verify_password
from backend.core.context import *
from backend.schemas import *

from backend.agent import AgentConfig
from backend.services.agent_runtime import get_agent_runtime as _default_get_agent_runtime
from backend.services.analytics import internal_project_snapshot

router = APIRouter()
logger = logging.getLogger("collab_ledger.agent")


def get_agent_runtime():
    main_module = sys.modules.get("backend.main")
    override = getattr(main_module, "get_agent_runtime", None) if main_module else None
    if override is not None and override is not get_agent_runtime:
        return override()
    return _default_get_agent_runtime()


def _has_agent_memory(conn) -> bool:
    """跨 SQLite/PostgreSQL 检查 Agent 记忆表，避免依赖 sqlite_master。"""
    try:
        conn.execute("SELECT 1 FROM agent_memory LIMIT 1").fetchone()
        return True
    except Exception:
        return False


@router.get("/api/agent/config")
def agent_config(request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request); conn.close(); return AgentConfig.from_env().public_dict()


@router.post("/api/projects/{project_id}/agent/chat")
def project_agent_chat(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    conn = db(); _, user, _ = ensure_project_access(conn, project_id, request, "member"); conn.close()
    try:
        result = get_agent_runtime().run(
            project_id,
            payload.message,
            payload.session_id,
            user_id=user["id"] if user else None,
        )
    except Exception:
        # Agent 运行异常不能落成无上下文的 500；记录堆栈并返回可识别服务错误。
        logger.exception(
            "agent chat failed: project_id=%s session_id=%s",
            project_id,
            payload.session_id,
        )
        fail(503, "AGENT_UNAVAILABLE", "Agent 服务暂时不可用，请检查后端日志或稍后重试")
    facts = result.get("facts") or {}
    facts.setdefault("project_id", project_id)
    facts.setdefault("risk_count", (facts.get("risks") or {}).get("count", 0))
    result["facts"] = facts
    return {**result, "generated_at": now_iso()}


@router.get("/api/projects/{project_id}/agent/sessions")
def agent_sessions(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); _, user, _ = ensure_project_access(conn, project_id, request)
    user_id = user["id"] if user else None
    if not _has_agent_memory(conn):
        conn.close()
        return {"items": []}
    user_clause = "user_id IS NULL" if user_id is None else "user_id=?"
    user_args = () if user_id is None else (user_id,)
    rows = conn.execute(
        f"""SELECT a.session_id,COUNT(*) message_count,MAX(a.created_at) updated_at,
        (SELECT content FROM agent_memory a2 WHERE a2.project_id=a.project_id
          AND a2.session_id=a.session_id AND {user_clause} ORDER BY id DESC LIMIT 1) last_message
        FROM agent_memory a
        WHERE a.project_id=? AND {user_clause}
        GROUP BY a.session_id ORDER BY updated_at DESC""",
        (*user_args, project_id, *user_args),
    ).fetchall()
    metadata_rows = conn.execute(
        f"SELECT session_key,title,created_at,updated_at FROM agent_sessions WHERE project_id=? AND {user_clause}",
        (project_id, *user_args),
    ).fetchall()
    conn.close()
    metadata = {row["session_key"]: row for row in metadata_rows}
    items = []
    for row in rows:
        item = dict(row)
        meta = metadata.pop(item["session_id"], None)
        item["title"] = meta["title"] if meta else None
        items.append(item)
    items.extend(
        {
            "session_id": row["session_key"],
            "title": row["title"],
            "message_count": 0,
            "updated_at": row["updated_at"] or row["created_at"],
            "last_message": None,
        }
        for row in metadata.values()
    )
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"items": items}


@router.patch("/api/projects/{project_id}/agent/sessions/{session_id}")
def rename_agent_session(
    project_id: int,
    session_id: str,
    payload: AgentSessionRenameIn,
    request: Request,
) -> dict[str, Any]:
    """重命名当前用户的 Agent 会话元数据。"""
    conn = db()
    _, user, _ = ensure_project_access(conn, project_id, request, "member")
    title = payload.title.strip()
    if not title:
        conn.close()
        fail(422, "VALIDATION_ERROR", "会话名称不能为空")
    stamp = now_iso()
    user_id = user["id"] if user else None
    if user_id is None:
        session = conn.execute(
            "SELECT id FROM agent_sessions WHERE project_id=? AND user_id IS NULL AND session_key=?",
            (project_id, session_id),
        ).fetchone()
    else:
        session = conn.execute(
            "SELECT id FROM agent_sessions WHERE project_id=? AND user_id=? AND session_key=?",
            (project_id, user_id, session_id),
        ).fetchone()
    if session:
        conn.execute(
            "UPDATE agent_sessions SET title=?,updated_at=? WHERE id=?",
            (title, stamp, session["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO agent_sessions(project_id,user_id,session_key,title,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (project_id, user_id, session_id, title, stamp, stamp),
        )
    conn.commit()
    conn.close()
    return {"project_id": project_id, "session_id": session_id, "title": title, "updated_at": stamp}


@router.get("/api/projects/{project_id}/agent/sessions/{session_id}/messages")
def agent_session_messages(project_id: int, session_id: str, request: Request) -> dict[str, Any]:
    """读取当前用户指定 Agent 会话历史，供前端切换会话时恢复消息。"""
    conn = db(); _, user, _ = ensure_project_access(conn, project_id, request)
    user_id = user["id"] if user else None
    if not _has_agent_memory(conn):
        conn.close()
        return {"session_id": session_id, "items": []}
    user_clause = "user_id IS NULL" if user_id is None else "user_id=?"
    user_args = () if user_id is None else (user_id,)
    rows = conn.execute(
        f"SELECT role,content,created_at FROM agent_memory "
        f"WHERE project_id=? AND session_id=? AND {user_clause} ORDER BY id ASC",
        (project_id, session_id, *user_args),
    ).fetchall()
    conn.close()
    return {"session_id": session_id, "items": [dict(row) for row in rows]}


@router.delete("/api/projects/{project_id}/agent/sessions/{session_id}", status_code=204)
def clear_agent_session(project_id: int, session_id: str, request: Request) -> Response:
    conn = db(); _, user, _ = ensure_project_access(conn, project_id, request, "owner")
    user_id = user["id"] if user else None
    if _has_agent_memory(conn):
        user_clause = "user_id IS NULL" if user_id is None else "user_id=?"
        user_args = () if user_id is None else (user_id,)
        conn.execute(
            f"DELETE FROM agent_memory WHERE project_id=? AND session_id=? AND {user_clause}",
            (project_id, session_id, *user_args),
        )
    if user_id is None:
        conn.execute(
            "DELETE FROM agent_sessions WHERE project_id=? AND user_id IS NULL AND session_key=?",
            (project_id, session_id),
        )
    else:
        conn.execute(
            "DELETE FROM agent_sessions WHERE project_id=? AND user_id=? AND session_key=?",
            (project_id, user_id, session_id),
        )
    conn.commit()
    conn.close(); return Response(status_code=204)


@router.post("/api/projects/{project_id}/agent")
def agent(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    return project_agent_chat(project_id, payload, request)


@router.post("/api/agent/chat")
def agent_chat(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    return project_agent_chat(project_id, payload, request)

__all__ = ['agent_config', 'project_agent_chat', 'agent_sessions', 'agent_session_messages', 'clear_agent_session', 'agent', 'agent_chat']
