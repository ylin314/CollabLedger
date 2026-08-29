from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from pydantic import BaseModel

from backend.auth import SessionError, current_user, iso_utc
from backend.db import connect as connect_db, initialize as initialize_database
from backend.core.errors import APIError, error_payload, fail
from backend.repositories.entities import contribution_row, task_row

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("COLLAB_DB", ROOT / "collab.db"))
ROLE_LEVEL = {"viewer": 1, "member": 2, "owner": 3}
TASK_STATUSES = {"unassigned", "assigned", "in_progress", "paused", "completed", "overdue", "unfinished"}
TASK_PRIORITIES = {"low", "medium", "high"}
CONTRIBUTION_KINDS = {"code", "document", "meeting", "research", "test", "design", "other"}
CONTRIBUTION_STATUSES = {"pending", "confirmed", "disputed"}

def now_iso() -> str:
    return iso_utc()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def active_db_path() -> Path:
    main_module = sys.modules.get("backend.main")
    return Path(getattr(main_module, "DB_PATH", DB_PATH))


def db():
    return connect_db(active_db_path())


def init_db() -> None:
    initialize_database(active_db_path())


def _dump(model: BaseModel, *, exclude_none: bool = True) -> dict[str, Any]:
    data = model.model_dump(exclude_none=exclude_none)
    for key, value in list(data.items()):
        if isinstance(value, (date, datetime)):
            data[key] = value.isoformat().replace("+00:00", "Z")
    return data


def _email_ok(email: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()))


def public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    try:
        out["skills"] = json.loads(out.get("skills") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["skills"] = []
    for key in ("password_hash", "token_hash", "session_expires_at", "updated_at"):
        out.pop(key, None)
    return {key: out.get(key) for key in ("id", "name", "email", "skills", "max_concurrent_tasks", "status", "created_at") if key in out}


def require_user(conn: sqlite3.Connection, request: Optional[Request]) -> sqlite3.Row:
    try:
        user = current_user(conn, request, required=True)
    except SessionError:
        conn.close()
        raise
    assert user is not None
    return user


def ensure_project(conn: sqlite3.Connection, project_id: int, *, include_deleted: bool = False) -> sqlite3.Row:
    sql = "SELECT * FROM projects WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL")
    row = conn.execute(sql, (project_id,)).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "项目不存在")
    return row


def project_role(conn: sqlite3.Connection, project_id: int, user_id: int) -> Optional[str]:
    row = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=? AND status='active'", (project_id, user_id)).fetchone()
    return row["role"] if row else None


def ensure_project_access(conn: sqlite3.Connection, project_id: int, request: Optional[Request], minimum_role: str = "viewer", *, allow_internal: bool = False) -> tuple[sqlite3.Row, Optional[sqlite3.Row], Optional[str]]:
    project = ensure_project(conn, project_id)
    if request is None and allow_internal:
        return project, None, None
    user = require_user(conn, request)
    role = project_role(conn, project_id, user["id"])
    if not role:
        fail(403, "FORBIDDEN", "没有该项目的访问权限")
    if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum_role]:
        fail(403, "FORBIDDEN", "角色权限不足")
    return project, user, role


def ensure_member(conn: sqlite3.Connection, project_id: int, user_id: int) -> None:
    if not conn.execute("SELECT 1 FROM memberships WHERE project_id=? AND user_id=? AND status='active'", (project_id, user_id)).fetchone():
        fail(400, "BAD_REQUEST", "用户不是该项目成员")


def ensure_classroom_member(conn: sqlite3.Connection, classroom_id: int, user_id: int) -> None:
    if not conn.execute("SELECT 1 FROM classroom_memberships WHERE classroom_id=? AND user_id=? AND status='active'", (classroom_id, user_id)).fetchone():
        fail(403, "FORBIDDEN", "用户不在该班级成员池中")


def task_participant_ids(conn: sqlite3.Connection, task_id: int) -> list[int]:
    return [row["user_id"] for row in conn.execute("SELECT user_id FROM task_participants WHERE task_id=? AND status='active'", (task_id,)).fetchall()]


def sync_task_participants(conn: sqlite3.Connection, task_id: int, participant_ids: list[int], assignee_id: Optional[int]) -> None:
    desired = set(participant_ids)
    if assignee_id is not None:
        desired.add(assignee_id)
    now = now_iso()
    current = {row["user_id"] for row in conn.execute("SELECT user_id FROM task_participants WHERE task_id=? AND status='active'", (task_id,)).fetchall()}
    for user_id in desired - current:
        role = "lead" if user_id == assignee_id else "collaborator"
        conn.execute("INSERT INTO task_participants(task_id,user_id,role,joined_at,status,updated_at) VALUES (?,?,?,?, 'active',?) ON CONFLICT(task_id,user_id) DO UPDATE SET role=excluded.role,status='active',left_at=NULL,updated_at=excluded.updated_at", (task_id, user_id, role, now, now))
    for user_id in current - desired:
        conn.execute("UPDATE task_participants SET status='left',left_at=?,updated_at=? WHERE task_id=? AND user_id=?", (now, now, task_id, user_id))


def ensure_writable(project: sqlite3.Row) -> None:
    if project["status"] == "archived":
        fail(409, "CONFLICT", "归档项目为只读状态")


def pagination(page: int, page_size: int) -> tuple[int, int]:
    return (page - 1) * page_size, page_size


def as_task(row: sqlite3.Row | dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    out = dict(row)
    result = {key: out.get(key) for key in ("id", "project_id", "title", "description", "assignee_id", "assignee_name", "status", "task_type", "priority", "due_date", "estimated_hours", "actual_hours", "quality", "reviewer_id", "reviewer_name", "created_by", "created_at", "updated_at")}
    result["participant_ids"] = task_participant_ids(conn, result["id"]) if conn is not None else []
    if conn is not None and result["participant_ids"]:
        placeholders = ",".join("?" for _ in result["participant_ids"])
        result["participants"] = [dict(r) for r in conn.execute(f"SELECT id,name,email FROM users WHERE id IN ({placeholders})", result["participant_ids"]).fetchall()]
    else:
        result["participants"] = []
    return result


def as_contribution(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    return {key: out.get(key) for key in ("id", "project_id", "user_id", "user_name", "kind", "title", "description", "quantity", "evidence_url", "status", "source", "occurred_at", "created_at", "updated_at")}


def _project_stats(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    counts = conn.execute(
        """SELECT COUNT(*) task_count,
        SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed_task_count,
        SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) in_progress_task_count,
        SUM(CASE WHEN status IN ('overdue','unfinished') THEN 1 ELSE 0 END) overdue_task_count
        FROM tasks WHERE project_id=? AND deleted_at IS NULL""", (project_id,)
    ).fetchone()
    member_count = conn.execute("SELECT COUNT(*) n FROM memberships WHERE project_id=? AND status='active'", (project_id,)).fetchone()["n"]
    total = counts["task_count"] or 0
    completed = counts["completed_task_count"] or 0
    return {
        "member_count": member_count, "task_count": total, "completed_task_count": completed,
        "in_progress_task_count": counts["in_progress_task_count"] or 0,
        "overdue_task_count": counts["overdue_task_count"] or 0,
        "progress": round(completed * 100 / total) if total else 0,
    }


def _project_detail(conn: sqlite3.Connection, project: sqlite3.Row, role: Optional[str]) -> dict[str, Any]:
    out = {key: project[key] for key in ("id", "name", "project_type", "description", "start_date", "end_date", "status", "owner_id", "classroom_id", "created_at", "updated_at")}
    if project["classroom_id"]:
        classroom = conn.execute("SELECT id,name,description FROM classrooms WHERE id=?", (project["classroom_id"],)).fetchone()
        out["classroom"] = dict(classroom) if classroom else None
    out["current_user_role"] = role
    out["statistics"] = _project_stats(conn, project["id"])
    return out

__all__ = ["ROOT", "DB_PATH", "ROLE_LEVEL", "TASK_STATUSES", "TASK_PRIORITIES", "CONTRIBUTION_KINDS", "CONTRIBUTION_STATUSES", "SessionError", "task_row", "contribution_row", "active_db_path", 'now_iso', 'utc_today', 'db', 'init_db', 'APIError', 'fail', 'error_payload', '_dump', '_email_ok', 'public_user', 'require_user', 'ensure_project', 'project_role', 'ensure_project_access', 'ensure_member', 'ensure_classroom_member', 'task_participant_ids', 'sync_task_participants', 'ensure_writable', 'pagination', 'as_task', 'as_contribution', '_project_stats', '_project_detail']
