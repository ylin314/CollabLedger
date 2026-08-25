from __future__ import annotations

import json
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


def get_agent_runtime():
    main_module = sys.modules.get("backend.main")
    override = getattr(main_module, "get_agent_runtime", None) if main_module else None
    if override is not None and override is not get_agent_runtime:
        return override()
    return _default_get_agent_runtime()


@router.get("/api/agent/config")
def agent_config(request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request); conn.close(); return AgentConfig.from_env().public_dict()


@router.post("/api/projects/{project_id}/agent/chat")
def project_agent_chat(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request, "member"); conn.close()
    result = get_agent_runtime().run(project_id, payload.message, payload.session_id)
    facts = result.get("facts") or {}
    facts.setdefault("project_id", project_id)
    facts.setdefault("risk_count", (facts.get("risks") or {}).get("count", 0))
    result["facts"] = facts
    return {**result, "generated_at": now_iso()}


@router.get("/api/projects/{project_id}/agent/sessions")
def agent_sessions(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    # AgentMemory 会在首次调用时建表；尚未调用时返回空列表。
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_memory'").fetchone()
    if not exists: conn.close(); return {"items": []}
    rows = conn.execute("""SELECT session_id,COUNT(*) message_count,MAX(created_at) updated_at,
        (SELECT content FROM agent_memory a2 WHERE a2.project_id=a.project_id AND a2.session_id=a.session_id ORDER BY id DESC LIMIT 1) last_message
        FROM agent_memory a WHERE project_id=? GROUP BY session_id ORDER BY updated_at DESC""", (project_id,)).fetchall(); conn.close(); return {"items": [dict(row) for row in rows]}


@router.delete("/api/projects/{project_id}/agent/sessions/{session_id}", status_code=204)
def clear_agent_session(project_id: int, session_id: str, request: Request) -> Response:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_memory'").fetchone(): conn.execute("DELETE FROM agent_memory WHERE project_id=? AND session_id=?", (project_id, session_id)); conn.commit()
    conn.close(); return Response(status_code=204)


@router.post("/api/projects/{project_id}/agent")
def agent(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    return project_agent_chat(project_id, payload, request)


@router.post("/api/agent/chat")
def agent_chat(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    return project_agent_chat(project_id, payload, request)

__all__ = ['agent_config', 'project_agent_chat', 'agent_sessions', 'clear_agent_session', 'agent', 'agent_chat']
