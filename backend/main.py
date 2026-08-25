from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent import AgentConfig, AgentRuntime
from backend.auth import COOKIE_NAME, SessionError, create_session, current_user, hash_password, iso_utc, revoke_session, verify_password

ROOT = Path(__file__).resolve().parent.parent
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


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_columns(conn: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = _columns(conn, table)
    for name, definition in definitions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db() -> None:
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            skills TEXT NOT NULL DEFAULT '[]',
            max_concurrent_tasks INTEGER NOT NULL DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'offline',
            password_hash TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            project_type TEXT,
            description TEXT,
            start_date TEXT,
            end_date TEXT,
            owner_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS memberships (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            PRIMARY KEY(project_id, user_id),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            assignee_id INTEGER,
            status TEXT NOT NULL DEFAULT 'unassigned',
            due_date TEXT,
            estimated_hours REAL,
            actual_hours REAL,
            quality REAL,
            task_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(assignee_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            note TEXT,
            at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT,
            description TEXT,
            quantity REAL NOT NULL DEFAULT 1,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS project_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            inviter_id INTEGER NOT NULL,
            invite_hash TEXT NOT NULL UNIQUE,
            invite_code TEXT NOT NULL UNIQUE,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'member',
            expires_at TEXT NOT NULL,
            accepted_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(inviter_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            work_date TEXT NOT NULL,
            hours REAL NOT NULL DEFAULT 0,
            note TEXT,
            check_in TEXT,
            check_out TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(project_id, user_id, work_date)
        );
        CREATE TABLE IF NOT EXISTS quality_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            task_id INTEGER,
            reviewer_id INTEGER NOT NULL,
            reviewee_id INTEGER NOT NULL,
            score REAL NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
            FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(reviewee_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(project_id, task_id, reviewer_id, reviewee_id)
        );
        CREATE TABLE IF NOT EXISTS task_checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            hours REAL NOT NULL,
            blockers TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS task_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL UNIQUE,
            reviewer_id INTEGER NOT NULL,
            quality REAL NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS task_review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            quality REAL NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    legacy_contributions = "status" not in _columns(conn, "contributions")
    _add_columns(conn, "users", {"password_hash": "TEXT", "updated_at": "TEXT"})
    _add_columns(conn, "projects", {
        "status": "TEXT NOT NULL DEFAULT 'active'", "updated_at": "TEXT", "archived_at": "TEXT", "deleted_at": "TEXT"
    })
    _add_columns(conn, "memberships", {"updated_at": "TEXT"})
    _add_columns(conn, "tasks", {
        "priority": "TEXT NOT NULL DEFAULT 'medium'", "created_by": "INTEGER", "deleted_at": "TEXT"
    })
    _add_columns(conn, "task_logs", {"from_status": "TEXT", "to_status": "TEXT"})
    _add_columns(conn, "project_invitations", {
        "max_uses": "INTEGER NOT NULL DEFAULT 1", "used_count": "INTEGER NOT NULL DEFAULT 0",
        "revoked": "INTEGER NOT NULL DEFAULT 0", "revoked_at": "TEXT", "updated_at": "TEXT"
    })
    _add_columns(conn, "contributions", {
        "evidence_url": "TEXT", "status": "TEXT NOT NULL DEFAULT 'pending'", "source": "TEXT NOT NULL DEFAULT 'manual'",
        "occurred_at": "TEXT", "updated_at": "TEXT", "created_by": "INTEGER", "confirmed_by": "INTEGER",
        "confirmed_at": "TEXT", "confirmation_note": "TEXT", "dispute_note": "TEXT", "deleted_at": "TEXT"
    })
    stamp = now_iso()
    conn.execute("UPDATE users SET updated_at=COALESCE(updated_at, created_at, ?)", (stamp,))
    conn.execute("UPDATE projects SET updated_at=COALESCE(updated_at, created_at, ?), status=COALESCE(status,'active')", (stamp,))
    conn.execute("UPDATE memberships SET updated_at=COALESCE(updated_at, joined_at, ?)", (stamp,))
    conn.execute("UPDATE contributions SET occurred_at=COALESCE(occurred_at,created_at,?), updated_at=COALESCE(updated_at,created_at,?), created_by=COALESCE(created_by,user_id)", (stamp, stamp))
    if legacy_contributions:
        conn.execute("""UPDATE contributions SET status='confirmed',confirmed_at=COALESCE(confirmed_at,created_at,?),
                     confirmed_by=COALESCE(confirmed_by,(SELECT owner_id FROM projects WHERE projects.id=contributions.project_id))""", (stamp,))
    conn.execute("UPDATE tasks SET priority=COALESCE(priority,'medium'), created_by=COALESCE(created_by,assignee_id)")
    conn.execute("UPDATE project_invitations SET used_count=CASE WHEN accepted_at IS NOT NULL AND used_count=0 THEN 1 ELSE used_count END")
    timestamp_columns = {
        "users": ("created_at", "updated_at"),
        "projects": ("created_at", "updated_at", "archived_at", "deleted_at"),
        "memberships": ("joined_at", "updated_at"),
        "tasks": ("created_at", "updated_at", "deleted_at"),
        "task_logs": ("at",),
        "contributions": ("occurred_at", "created_at", "updated_at", "confirmed_at", "deleted_at"),
        "project_invitations": ("expires_at", "accepted_at", "created_at", "updated_at", "revoked_at"),
        "auth_sessions": ("created_at", "expires_at", "revoked_at"),
        "work_logs": ("check_in", "check_out", "created_at", "updated_at"),
        "quality_reviews": ("created_at", "updated_at"),
    }
    for table, names in timestamp_columns.items():
        existing = _columns(conn, table)
        for name in names:
            if name in existing:
                conn.execute(f"UPDATE {table} SET {name}=replace({name}, '+00:00', 'Z') WHERE {name} LIKE '%+00:00'")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id, project_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, deleted_at, status);
        CREATE INDEX IF NOT EXISTS idx_checkins_project ON task_checkins(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_contributions_project ON contributions(project_id, deleted_at, status);
        """
    )
    conn.commit()
    conn.close()


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[list[dict[str, Any]]] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def fail(status_code: int, code: str, message: str, details: Optional[list[dict[str, Any]]] = None) -> None:
    raise APIError(status_code, code, message, details)


def error_payload(code: str, message: str, details: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="协作账本 API", version="1.0.0", description="面向小组作业的贡献留痕与智能协作 API", lifespan=lifespan)
origins = [item.strip() for item in os.getenv("COLLAB_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(APIError)
async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, exc.details))


@app.exception_handler(SessionError)
async def session_error_handler(_: Request, exc: SessionError) -> JSONResponse:
    return JSONResponse(status_code=401, content=error_payload("UNAUTHORIZED", str(exc)))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", ()) if part not in ("body", "query", "path")]
        details.append({"field": ".".join(loc) or "request", "message": "字段格式不正确"})
    return JSONResponse(status_code=422, content=error_payload("VALIDATION_ERROR", "请求参数不正确", details))


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=error_payload("INTERNAL_ERROR", "服务器内部错误"))


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    codes = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 502: "LLM_PROVIDER_ERROR"}
    message = exc.detail if isinstance(exc.detail, str) else "请求失败"
    if exc.status_code == 404 and message == "Not Found":
        message = "资源不存在"
    elif exc.status_code == 405 and message == "Method Not Allowed":
        message = "请求方法不支持"
    return JSONResponse(status_code=exc.status_code, content=error_payload(codes.get(exc.status_code, "BAD_REQUEST"), message))


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)
    status: str = "offline"
    password: Optional[str] = Field(default=None, min_length=8)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    skills: Optional[list[str]] = None
    max_concurrent_tasks: Optional[int] = Field(default=None, ge=1, le=100)
    status: Optional[Literal["online", "offline", "busy"]] = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=5000)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    owner_id: Optional[int] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    project_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=5000)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class MemberIn(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    role: Literal["member", "viewer"] = "member"
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)


class RoleUpdate(BaseModel):
    role: Literal["owner", "member", "viewer"]


class InvitationIn(BaseModel):
    role: Literal["member", "viewer"] = "member"
    expires_in_hours: int = Field(default=168, ge=1, le=24 * 365)
    max_uses: int = Field(default=10, ge=1, le=10000)
    email: Optional[str] = None
    expires_days: Optional[int] = Field(default=None, ge=1, le=365)


class AcceptInvitationIn(BaseModel):
    token: Optional[str] = None
    invite_code: Optional[str] = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    assignee_id: Optional[int] = None
    task_type: Optional[str] = Field(default=None, max_length=100)
    priority: Literal["low", "medium", "high"] = "medium"
    due_date: Optional[date] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    status: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    assignee_id: Optional[int] = None
    task_type: Optional[str] = Field(default=None, max_length=100)
    priority: Optional[Literal["low", "medium", "high"]] = None
    due_date: Optional[date] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    actual_hours: Optional[float] = Field(default=None, ge=0)
    quality: Optional[float] = Field(default=None, ge=0, le=5)
    status: Optional[str] = None
    user_id: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=1000)


class AssignIn(BaseModel):
    assignee_id: int
    note: Optional[str] = Field(default=None, max_length=1000)


class TaskActionIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    actual_hours: Optional[float] = Field(default=None, ge=0)


class CheckinIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    hours: float = Field(ge=0, le=24)
    blockers: Optional[str] = Field(default=None, max_length=1000)


class ReviewIn(BaseModel):
    quality: float = Field(ge=0, le=5)
    comment: Optional[str] = Field(default=None, max_length=1000)


class ContributionIn(BaseModel):
    user_id: Optional[int] = None
    kind: Literal["code", "document", "meeting", "research", "test", "design", "other"] = "other"
    title: str = Field(min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    quantity: float = Field(default=1, ge=0)
    evidence_url: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContributionUpdate(BaseModel):
    kind: Optional[Literal["code", "document", "meeting", "research", "test", "design", "other"]] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    quantity: Optional[float] = Field(default=None, ge=0)
    evidence_url: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[datetime] = None


class NoteIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class AgentIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=100)


class WorkLogIn(BaseModel):
    work_date: Optional[date] = None
    hours: float = Field(default=0, ge=0, le=24)
    note: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None


class QualityReviewIn(BaseModel):
    task_id: Optional[int] = None
    reviewee_id: int
    score: float = Field(ge=0, le=5)
    comment: Optional[str] = None


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
    row = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
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
    if not conn.execute("SELECT 1 FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone():
        fail(400, "BAD_REQUEST", "用户不是该项目成员")


def ensure_writable(project: sqlite3.Row) -> None:
    if project["status"] == "archived":
        fail(409, "CONFLICT", "归档项目为只读状态")


def pagination(page: int, page_size: int) -> tuple[int, int]:
    return (page - 1) * page_size, page_size


def task_row(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.id=? AND t.deleted_at IS NULL", (task_id,)).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "任务不存在")
    return row


def as_task(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    return {key: out.get(key) for key in ("id", "project_id", "title", "description", "assignee_id", "assignee_name", "status", "task_type", "priority", "due_date", "estimated_hours", "actual_hours", "quality", "created_by", "created_at", "updated_at")}


def contribution_row(conn: sqlite3.Connection, contribution_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT c.*,u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.id=? AND c.deleted_at IS NULL", (contribution_id,)).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "贡献记录不存在")
    return row


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
    member_count = conn.execute("SELECT COUNT(*) n FROM memberships WHERE project_id=?", (project_id,)).fetchone()["n"]
    total = counts["task_count"] or 0
    completed = counts["completed_task_count"] or 0
    return {
        "member_count": member_count, "task_count": total, "completed_task_count": completed,
        "in_progress_task_count": counts["in_progress_task_count"] or 0,
        "overdue_task_count": counts["overdue_task_count"] or 0,
        "progress": round(completed * 100 / total) if total else 0,
    }


def _project_detail(conn: sqlite3.Connection, project: sqlite3.Row, role: Optional[str]) -> dict[str, Any]:
    out = {key: project[key] for key in ("id", "name", "project_type", "description", "start_date", "end_date", "status", "owner_id", "created_at", "updated_at")}
    out["current_user_role"] = role
    out["statistics"] = _project_stats(conn, project["id"])
    return out


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "collab-ledger"}


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterIn) -> dict[str, Any]:
    if not payload.name.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "用户名称不能为空"}])
    if not _email_ok(payload.email):
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "email", "message": "邮箱格式不正确"}])
    conn = db()
    if conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (payload.email.strip(),)).fetchone():
        conn.close()
        fail(409, "CONFLICT", "邮箱已被注册")
    stamp = now_iso()
    cur = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (payload.name.strip(), payload.email.strip().lower(), "[]", 3, "offline", hash_password(payload.password), stamp, stamp),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return public_user(row)


@app.post("/api/auth/login")
def login(payload: LoginIn, response: Response, request: Request) -> dict[str, Any]:
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (payload.email.strip(),)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        conn.close()
        fail(401, "UNAUTHORIZED", "邮箱或密码错误")
    token, expires_at = create_session(conn, row["id"])
    conn.execute("UPDATE users SET status='online',updated_at=? WHERE id=?", (now_iso(), row["id"]))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    conn.close()
    secure_setting = os.getenv("COLLAB_COOKIE_SECURE", "auto").lower()
    secure = secure_setting == "true" or (secure_setting == "auto" and request.url.scheme == "https")
    response.set_cookie(
        COOKIE_NAME, token, max_age=7 * 24 * 3600, expires=expires_at,
        httponly=True, secure=secure, samesite="lax", path="/",
    )
    return {"user": public_user(row)}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    conn = db()
    user = require_user(conn, request)
    revoke_session(conn, request)
    conn.execute("UPDATE users SET status='offline',updated_at=? WHERE id=?", (now_iso(), user["id"]))
    conn.commit()
    conn.close()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax")
    response.status_code = 204
    return response


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    conn = db()
    user = require_user(conn, request)
    out = public_user(user)
    conn.close()
    return out


@app.patch("/api/users/me")
def update_me(payload: UserUpdate, request: Request) -> dict[str, Any]:
    if payload.name is not None and not payload.name.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "用户名称不能为空"}])
    conn = db()
    user = require_user(conn, request)
    data = _dump(payload)
    if data:
        if "skills" in data:
            data["skills"] = json.dumps(data["skills"], ensure_ascii=False)
        data["updated_at"] = now_iso()
        sets = ",".join(f"{key}=?" for key in data)
        conn.execute(f"UPDATE users SET {sets} WHERE id=?", (*data.values(), user["id"]))
        conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    conn.close()
    return public_user(row)


# 旧接口作为兼容入口；HTTP 调用仍要求登录，直接 Python 调用用于旧 Agent/测试迁移。
@app.post("/api/users", status_code=201)
def create_user(payload: UserIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db()
    if request is not None:
        require_user(conn, request)
    if payload.email and conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (payload.email,)).fetchone():
        conn.close()
        fail(409, "CONFLICT", "邮箱已被注册")
    stamp = now_iso()
    cur = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, payload.status, hash_password(payload.password) if payload.password else None, stamp, stamp),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return public_user(row)


@app.get("/api/users")
def list_users(request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request)
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall(); conn.close()
    return {"items": [public_user(row) for row in rows]}


@app.get("/api/users/{user_id}")
def get_user(user_id: int, request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request)
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(); conn.close()
    if not row: fail(404, "NOT_FOUND", "用户不存在")
    return public_user(row)


@app.get("/api/projects")
def list_projects(
    request: Request, archived: bool = False, keyword: Optional[str] = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request)
    where = ["m.user_id=?", "p.deleted_at IS NULL", "p.status=?"]
    args: list[Any] = [user["id"], "archived" if archived else "active"]
    if keyword:
        where.append("p.name LIKE ?"); args.append(f"%{keyword.strip()}%")
    condition = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) n FROM projects p JOIN memberships m ON m.project_id=p.id WHERE {condition}", args).fetchone()["n"]
    offset, limit = pagination(page, page_size)
    rows = conn.execute(
        f"""SELECT p.*,m.role,
        (SELECT COUNT(*) FROM memberships mm WHERE mm.project_id=p.id) member_count,
        (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.deleted_at IS NULL) task_count,
        (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.deleted_at IS NULL AND t.status='completed') completed_task_count
        FROM projects p JOIN memberships m ON m.project_id=p.id WHERE {condition}
        ORDER BY p.updated_at DESC,p.id DESC LIMIT ? OFFSET ?""", (*args, limit, offset)
    ).fetchall()
    conn.close()
    items = [{key: row[key] for key in ("id", "name", "project_type", "description", "start_date", "end_date", "status", "role", "member_count", "task_count", "completed_task_count", "created_at", "updated_at")} for row in rows]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    if not payload.name.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "项目名称不能为空"}])
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "end_date", "message": "结束日期不能早于开始日期"}])
    conn = db()
    user = require_user(conn, request) if request is not None else None
    owner_id = user["id"] if user is not None else payload.owner_id
    if owner_id is None:
        conn.close(); fail(401, "UNAUTHORIZED", "请先登录")
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone():
        conn.close(); fail(404, "NOT_FOUND", "用户不存在")
    stamp = now_iso()
    cur = conn.execute(
        "INSERT INTO projects(name,project_type,description,start_date,end_date,owner_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?, 'active',?,?)",
        (payload.name.strip(), payload.project_type, payload.description, payload.start_date.isoformat() if payload.start_date else None, payload.end_date.isoformat() if payload.end_date else None, owner_id, stamp, stamp),
    )
    conn.execute("INSERT INTO memberships(project_id,user_id,role,joined_at,updated_at) VALUES (?,?, 'owner',?,?)", (cur.lastrowid, owner_id, stamp, stamp))
    conn.commit()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone()
    out = _project_detail(conn, project, "owner")
    conn.close()
    return out


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db()
    project, _, role = ensure_project_access(conn, project_id, request, allow_internal=request is None)
    out = _project_detail(conn, project, role)
    conn.close()
    return out


@app.patch("/api/projects/{project_id}")
def update_project(project_id: int, payload: ProjectUpdate, request: Request) -> dict[str, Any]:
    if payload.name is not None and not payload.name.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "项目名称不能为空"}])
    conn = db(); project, _, role = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    data = _dump(payload)
    start = data.get("start_date", project["start_date"])
    end = data.get("end_date", project["end_date"])
    if start and end and end < start:
        conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "end_date", "message": "结束日期不能早于开始日期"}])
    if data:
        data["updated_at"] = now_iso()
        conn.execute(f"UPDATE projects SET {','.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), project_id)); conn.commit()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    out = _project_detail(conn, project, role); conn.close(); return out


@app.post("/api/projects/{project_id}/archive")
def archive_project(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request, "owner")
    if project["status"] == "archived": conn.close(); fail(409, "CONFLICT", "项目已归档")
    stamp = now_iso(); conn.execute("UPDATE projects SET status='archived',archived_at=?,updated_at=? WHERE id=?", (stamp, stamp, project_id)); conn.commit(); conn.close()
    return {"id": project_id, "status": "archived", "archived_at": stamp}


@app.post("/api/projects/{project_id}/restore")
def restore_project(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); project, _, role = ensure_project_access(conn, project_id, request, "owner")
    if project["status"] != "archived": conn.close(); fail(409, "CONFLICT", "项目未归档")
    stamp = now_iso(); conn.execute("UPDATE projects SET status='active',archived_at=NULL,updated_at=? WHERE id=?", (stamp, project_id)); conn.commit()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone(); out = _project_detail(conn, project, role); conn.close(); return out


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int, request: Request) -> Response:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    conn.execute("UPDATE projects SET deleted_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), project_id)); conn.commit(); conn.close()
    return Response(status_code=204)


@app.post("/api/projects/{project_id}/members", status_code=201)
def add_member(project_id: int, payload: MemberIn, request: Request) -> dict[str, Any]:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    user_id = payload.user_id
    if user_id is None:
        if not payload.name:
            conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "成员名称不能为空"}])
        stamp = now_iso(); cur = conn.execute("INSERT INTO users(name,email,skills,max_concurrent_tasks,status,created_at,updated_at) VALUES (?,?,?,?, 'offline',?,?)", (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, stamp, stamp)); user_id = cur.lastrowid
    elif not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        conn.close(); fail(404, "NOT_FOUND", "用户不存在")
    if conn.execute("SELECT 1 FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone():
        conn.close(); fail(409, "CONFLICT", "用户已是项目成员")
    stamp = now_iso(); conn.execute("INSERT INTO memberships(project_id,user_id,role,joined_at,updated_at) VALUES (?,?,?,?,?)", (project_id, user_id, payload.role, stamp, stamp)); conn.commit()
    row = conn.execute("SELECT m.user_id,u.name,u.email,m.role,u.skills,u.max_concurrent_tasks,u.status,m.joined_at FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND m.user_id=?", (project_id, user_id)).fetchone(); conn.close()
    out = dict(row); out["skills"] = json.loads(out["skills"] or "[]"); out["current_task_count"] = 0; return out


@app.get("/api/projects/{project_id}/members")
def list_members(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    rows = conn.execute(
        """SELECT m.user_id,u.name,u.email,m.role,u.skills,u.max_concurrent_tasks,u.status,m.joined_at,
        (SELECT COUNT(*) FROM tasks t WHERE t.project_id=m.project_id AND t.assignee_id=m.user_id AND t.deleted_at IS NULL AND t.status IN ('assigned','in_progress','paused','overdue')) current_task_count
        FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'member' THEN 1 ELSE 2 END,m.joined_at""", (project_id,)
    ).fetchall(); conn.close()
    items = []
    for row in rows:
        item = dict(row); item["skills"] = json.loads(item["skills"] or "[]"); items.append(item)
    return {"items": items}


@app.patch("/api/projects/{project_id}/members/{user_id:int}")
def update_member_role(project_id: int, user_id: int, payload: RoleUpdate, request: Request) -> dict[str, Any]:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    current = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
    if not current: conn.close(); fail(404, "NOT_FOUND", "项目成员不存在")
    if current["role"] == "owner" and payload.role != "owner":
        owner_count = conn.execute("SELECT COUNT(*) n FROM memberships WHERE project_id=? AND role='owner'", (project_id,)).fetchone()["n"]
        if owner_count <= 1: conn.close(); fail(409, "CONFLICT", "项目必须至少保留一个 owner")
    stamp = now_iso(); conn.execute("UPDATE memberships SET role=?,updated_at=? WHERE project_id=? AND user_id=?", (payload.role, stamp, project_id, user_id))
    if project["owner_id"] == user_id and payload.role != "owner":
        replacement = conn.execute("SELECT user_id FROM memberships WHERE project_id=? AND role='owner' ORDER BY joined_at LIMIT 1", (project_id,)).fetchone()
        conn.execute("UPDATE projects SET owner_id=?,updated_at=? WHERE id=?", (replacement["user_id"], stamp, project_id))
    conn.commit(); conn.close(); return {"user_id": user_id, "role": payload.role, "updated_at": stamp}


@app.delete("/api/projects/{project_id}/members/{user_id:int}", status_code=204)
def remove_member(project_id: int, user_id: int, request: Request) -> Response:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project)
    row = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
    if not row: conn.close(); fail(404, "NOT_FOUND", "项目成员不存在")
    if row["role"] == "owner":
        count = conn.execute("SELECT COUNT(*) n FROM memberships WHERE project_id=? AND role='owner'", (project_id,)).fetchone()["n"]
        if count <= 1: conn.close(); fail(409, "CONFLICT", "项目必须至少保留一个 owner")
    conn.execute("DELETE FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id))
    if project["owner_id"] == user_id:
        replacement = conn.execute("SELECT user_id FROM memberships WHERE project_id=? AND role='owner' ORDER BY joined_at LIMIT 1", (project_id,)).fetchone()
        conn.execute("UPDATE projects SET owner_id=?,updated_at=? WHERE id=?", (replacement["user_id"], now_iso(), project_id))
    conn.commit(); conn.close(); return Response(status_code=204)


def _invitation_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    return {"id": out["id"], "code": out["invite_code"], "role": out["role"], "expires_at": out["expires_at"], "max_uses": out["max_uses"], "used_count": out["used_count"], "revoked": bool(out["revoked"]), "invite_url": f"/invite/{out['invite_code']}"}


@app.post("/api/projects/{project_id}/invitations", status_code=201)
def create_invitation(project_id: int, payload: InvitationIn, request: Request) -> dict[str, Any]:
    conn = db(); project, user, _ = ensure_project_access(conn, project_id, request, "owner"); ensure_writable(project); assert user is not None
    code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].upper()
    hours = payload.expires_days * 24 if payload.expires_days else payload.expires_in_hours
    expires = iso_utc(datetime.now(timezone.utc) + timedelta(hours=hours)); stamp = now_iso()
    cur = conn.execute(
        """INSERT INTO project_invitations(project_id,inviter_id,invite_hash,invite_code,email,role,expires_at,accepted_at,created_at,max_uses,used_count,revoked,updated_at)
        VALUES (?,?,?,?,?,?,?,NULL,?,?,0,0,?)""",
        (project_id, user["id"], code, code, payload.email, payload.role, expires, stamp, payload.max_uses, stamp),
    ); conn.commit(); row = conn.execute("SELECT * FROM project_invitations WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close(); return _invitation_dict(row)


@app.get("/api/projects/{project_id}/invitations")
def list_invitations(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    rows = conn.execute("SELECT * FROM project_invitations WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall(); conn.close()
    return {"items": [_invitation_dict(row) for row in rows]}


@app.post("/api/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = conn.execute("SELECT * FROM project_invitations WHERE id=?", (invitation_id,)).fetchone()
    if not row: conn.close(); fail(404, "NOT_FOUND", "邀请不存在")
    project, _, _ = ensure_project_access(conn, row["project_id"], request, "owner"); ensure_writable(project)
    conn.execute("UPDATE project_invitations SET revoked=1,revoked_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), invitation_id)); conn.commit(); conn.close()
    return {"id": invitation_id, "revoked": True}


def _load_invitation(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row = conn.execute("SELECT i.*,p.name project_name,p.status project_status,p.deleted_at project_deleted_at FROM project_invitations i JOIN projects p ON p.id=i.project_id WHERE i.invite_code=? COLLATE NOCASE", (code,)).fetchone()
    if not row or row["project_deleted_at"] is not None: fail(404, "NOT_FOUND", "邀请不存在")
    return row


def _invitation_valid(row: sqlite3.Row) -> bool:
    try: not_expired = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except (TypeError, ValueError): not_expired = False
    return row["project_status"] == "active" and not bool(row["revoked"]) and row["used_count"] < row["max_uses"] and not_expired


@app.get("/api/invitations/{code}")
def get_invitation(code: str, request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request); row = _load_invitation(conn, code); valid = _invitation_valid(row); conn.close()
    return {"project_id": row["project_id"], "project_name": row["project_name"], "role": row["role"], "expires_at": row["expires_at"], "valid": valid}


def _accept_code(code: str, request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request); row = _load_invitation(conn, code)
    if conn.execute("SELECT 1 FROM memberships WHERE project_id=? AND user_id=?", (row["project_id"], user["id"])).fetchone():
        conn.close(); fail(409, "CONFLICT", "用户已是项目成员")
    if not _invitation_valid(row): conn.close(); fail(409, "CONFLICT", "邀请已过期、已撤销或已达到使用上限")
    if row["email"] and user["email"] and row["email"].lower() != user["email"].lower(): conn.close(); fail(403, "FORBIDDEN", "该邀请不适用于当前用户")
    stamp = now_iso(); conn.execute("INSERT INTO memberships(project_id,user_id,role,joined_at,updated_at) VALUES (?,?,?,?,?)", (row["project_id"], user["id"], row["role"], stamp, stamp))
    conn.execute("UPDATE project_invitations SET used_count=used_count+1,accepted_at=?,updated_at=? WHERE id=?", (stamp, stamp, row["id"])); conn.commit(); conn.close()
    return {"project_id": row["project_id"], "user_id": user["id"], "role": row["role"], "joined_at": stamp}


@app.post("/api/invitations/{code}/accept")
def accept_invitation_code(code: str, request: Request) -> dict[str, Any]:
    return _accept_code(code, request)


@app.post("/api/auth/accept-invitation")
def accept_invitation(payload: AcceptInvitationIn, request: Request) -> dict[str, Any]:
    code = payload.invite_code or payload.token
    if not code: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "code", "message": "邀请码不能为空"}])
    return _accept_code(code, request)

def _task_log(conn: sqlite3.Connection, task_id: int, user_id: Optional[int], action: str, from_status: Optional[str], to_status: Optional[str], note: Optional[str]) -> dict[str, Any]:
    stamp = now_iso()
    cur = conn.execute("INSERT INTO task_logs(task_id,user_id,action,from_status,to_status,note,at) VALUES (?,?,?,?,?,?,?)", (task_id, user_id, action, from_status, to_status, note, stamp))
    return {"id": cur.lastrowid, "action": action, "note": note, "user_id": user_id, "at": stamp}


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(
    project_id: int, request: Request, status: Optional[str] = None, assignee_id: Optional[int] = None,
    task_type: Optional[str] = None, keyword: Optional[str] = None, due_before: Optional[date] = None,
    overdue_only: bool = False, sort: Literal["due_date", "created_at", "priority"] = "created_at",
    order: Literal["asc", "desc"] = "desc", page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    where = ["t.project_id=?", "t.deleted_at IS NULL"]; args: list[Any] = [project_id]
    if status:
        if status not in TASK_STATUSES: conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "status", "message": "任务状态不正确"}])
        where.append("t.status=?"); args.append(status)
    if assignee_id is not None: where.append("t.assignee_id=?"); args.append(assignee_id)
    if task_type: where.append("t.task_type=?"); args.append(task_type)
    if keyword: where.append("(t.title LIKE ? OR COALESCE(t.description,'') LIKE ?)"); args.extend([f"%{keyword}%", f"%{keyword}%"])
    if due_before: where.append("t.due_date<?"); args.append(due_before.isoformat())
    if overdue_only: where.append("(t.status IN ('overdue','unfinished') OR (t.due_date<? AND t.status!='completed'))"); args.append(utc_today().isoformat())
    condition = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) n FROM tasks t WHERE {condition}", args).fetchone()["n"]
    sort_sql = "CASE t.priority WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END" if sort == "priority" else f"t.{sort}"
    offset, limit = pagination(page, page_size)
    rows = conn.execute(f"SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE {condition} ORDER BY {sort_sql} {order.upper()},t.id {order.upper()} LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall(); conn.close()
    return {"items": [as_task(row) for row in rows], "page": page, "page_size": page_size, "total": total}


@app.post("/api/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: int, payload: TaskIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    if not payload.title.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "title", "message": "任务标题不能为空"}])
    conn = db()
    project, user, role = ensure_project_access(conn, project_id, request, "member", allow_internal=request is None)
    ensure_writable(project)
    if payload.status and payload.status not in TASK_STATUSES: conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "status", "message": "任务状态不正确"}])
    if payload.assignee_id is not None: ensure_member(conn, project_id, payload.assignee_id)
    status = payload.status if request is None and payload.status else ("assigned" if payload.assignee_id is not None else "unassigned")
    actor_id = user["id"] if user is not None else (payload.owner_id if hasattr(payload, "owner_id") else None)
    stamp = now_iso()
    cur = conn.execute(
        """INSERT INTO tasks(project_id,title,description,assignee_id,status,due_date,estimated_hours,actual_hours,quality,task_type,priority,created_by,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?)""",
        (project_id, payload.title, payload.description, payload.assignee_id, status, payload.due_date.isoformat() if payload.due_date else None, payload.estimated_hours, payload.task_type, payload.priority, actor_id, stamp, stamp),
    )
    _task_log(conn, cur.lastrowid, actor_id, "created", None, status, None); conn.commit()
    row = conn.execute("SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.id=?", (cur.lastrowid,)).fetchone(); conn.close(); return as_task(row)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = task_row(conn, task_id); ensure_project_access(conn, row["project_id"], request); out = as_task(row); conn.close(); return out


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); row = task_row(conn, task_id)
    project, user, role = ensure_project_access(conn, row["project_id"], request, "member", allow_internal=request is None); ensure_writable(project)
    raw = payload.model_dump(exclude_unset=True)
    if "title" in raw and raw["title"] is not None and not raw["title"].strip():
        conn.close()
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "title", "message": "任务标题不能为空"}])
    raw.pop("user_id", None); note = raw.pop("note", None)
    if request is not None and ("status" in raw or "quality" in raw):
        conn.close()
        field = "status" if "status" in raw else "quality"
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": field, "message": "请使用专用状态或评价接口"}])
    for key, value in list(raw.items()):
        if isinstance(value, date): raw[key] = value.isoformat()
    if user is not None and role != "owner":
        if row["assignee_id"] != user["id"]: conn.close(); fail(403, "FORBIDDEN", "只能更新自己负责的任务")
        forbidden = set(raw) - {"actual_hours"}
        if forbidden: conn.close(); fail(403, "FORBIDDEN", "普通成员只能更新任务执行字段")
    if "status" in raw and raw["status"] not in TASK_STATUSES: conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "status", "message": "任务状态不正确"}])
    if "assignee_id" in raw and raw["assignee_id"] is not None: ensure_member(conn, row["project_id"], raw["assignee_id"])
    if "assignee_id" in raw and "status" not in raw:
        raw["status"] = "assigned" if raw["assignee_id"] is not None else "unassigned"
    before_status = row["status"]
    changed = {key: value for key, value in raw.items() if value != row[key]}
    if changed:
        changed["updated_at"] = now_iso(); conn.execute(f"UPDATE tasks SET {','.join(f'{k}=?' for k in changed)} WHERE id=?", (*changed.values(), task_id))
        actor = user["id"] if user is not None else payload.user_id
        action = "assigned" if "assignee_id" in changed else "updated"
        _task_log(conn, task_id, actor, action, before_status, changed.get("status", before_status), note or ("更新字段：" + "、".join(k for k in changed if k != "updated_at")))
    elif note:
        _task_log(conn, task_id, user["id"] if user is not None else payload.user_id, "updated", before_status, before_status, note)
    conn.commit(); row = task_row(conn, task_id); out = as_task(row); conn.close(); return out


@app.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: int, payload: AssignIn = None, request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    # Query 参数兼容旧客户端；契约客户端使用 JSON body。
    assignee_id = payload.assignee_id if payload is not None else user_id
    assignment_note = payload.note if payload is not None else note
    if assignee_id is None: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "assignee_id", "message": "负责人不能为空"}])
    conn = db(); row = task_row(conn, task_id); project, user, _ = ensure_project_access(conn, row["project_id"], request, "member", allow_internal=request is None); ensure_writable(project); ensure_member(conn, row["project_id"], assignee_id)
    before = row["status"]; stamp = now_iso(); conn.execute("UPDATE tasks SET assignee_id=?,status='assigned',updated_at=? WHERE id=?", (assignee_id, stamp, task_id))
    _task_log(conn, task_id, user["id"] if user is not None else assignee_id, "assigned", before, "assigned", assignment_note); conn.commit(); row = task_row(conn, task_id); out = as_task(row); conn.close(); return out


def _task_action(task_id: int, action: str, payload: TaskActionIn, request: Request, legacy_user_id: Optional[int] = None) -> dict[str, Any]:
    targets = {"start": "in_progress", "pause": "paused", "resume": "in_progress", "complete": "completed", "overdue": "overdue", "unfinished": "unfinished"}
    allowed = {
        "start": {"assigned"}, "pause": {"in_progress"}, "resume": {"paused"},
        "complete": {"assigned", "in_progress", "paused", "overdue"}, "overdue": {"assigned", "in_progress", "paused"},
        "unfinished": {"unassigned", "assigned", "in_progress", "paused", "overdue"},
    }
    conn = db(); row = task_row(conn, task_id); project, user, role = ensure_project_access(conn, row["project_id"], request, "member", allow_internal=request is None); ensure_writable(project)
    actor_id = user["id"] if user is not None else legacy_user_id
    if user is not None and role != "owner" and row["assignee_id"] != user["id"]: conn.close(); fail(403, "FORBIDDEN", "只有 owner 或任务负责人可以执行该操作")
    if action in ("start", "pause", "resume", "complete") and row["assignee_id"] is None: conn.close(); fail(409, "CONFLICT", "任务尚未指派负责人")
    if row["status"] not in allowed[action]: conn.close(); fail(409, "CONFLICT", f"当前状态不能执行{action}操作")
    target = targets[action]; stamp = now_iso(); values: list[Any] = [target, stamp]
    sql = "UPDATE tasks SET status=?,updated_at=?"
    if action == "complete" and payload.actual_hours is not None: sql += ",actual_hours=?"; values.append(payload.actual_hours)
    sql += " WHERE id=?"; values.append(task_id); conn.execute(sql, values)
    log = _task_log(conn, task_id, actor_id, action, row["status"], target, payload.note); conn.commit(); conn.close()
    return {"id": task_id, "status": target, "updated_at": stamp, "log": log}


@app.post("/api/tasks/{task_id}/start")
def start_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note, actual_hours=payload.actual_hours)
    return _task_action(task_id, "start", payload, request, user_id)


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "pause", payload, request, user_id)


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "resume", payload, request, user_id)


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note, actual_hours=payload.actual_hours)
    return _task_action(task_id, "complete", payload, request, user_id)


@app.post("/api/tasks/{task_id}/overdue")
def overdue_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "overdue", payload, request, user_id)


@app.post("/api/tasks/{task_id}/unfinished")
def unfinished_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "unfinished", payload, request, user_id)


@app.get("/api/tasks/{task_id}/logs")
def task_logs(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = task_row(conn, task_id); ensure_project_access(conn, row["project_id"], request)
    rows = conn.execute("SELECT l.id,l.task_id,l.user_id,u.name user_name,l.action,l.from_status,l.to_status,l.note,l.at FROM task_logs l LEFT JOIN users u ON u.id=l.user_id WHERE l.task_id=? ORDER BY l.id", (task_id,)).fetchall(); conn.close(); return {"items": [dict(item) for item in rows]}


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, request: Request) -> Response:
    conn = db(); row = task_row(conn, task_id); project, _, _ = ensure_project_access(conn, row["project_id"], request, "owner"); ensure_writable(project)
    conn.execute("UPDATE tasks SET deleted_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), task_id)); conn.commit(); conn.close(); return Response(status_code=204)


@app.post("/api/tasks/{task_id}/checkins", status_code=201)
def create_checkin(task_id: int, payload: CheckinIn, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); project, user, role = ensure_project_access(conn, task["project_id"], request, "member"); ensure_writable(project); assert user is not None
    if role != "owner" and task["assignee_id"] != user["id"]: conn.close(); fail(403, "FORBIDDEN", "只有 owner 或任务负责人可以打卡")
    stamp = now_iso(); cur = conn.execute("INSERT INTO task_checkins(task_id,project_id,user_id,content,hours,blockers,created_at) VALUES (?,?,?,?,?,?,?)", (task_id, task["project_id"], user["id"], payload.content, payload.hours, payload.blockers, stamp)); conn.commit()
    row = conn.execute("SELECT c.*,u.name user_name FROM task_checkins c JOIN users u ON u.id=c.user_id WHERE c.id=?", (cur.lastrowid,)).fetchone(); conn.close(); return dict(row)


@app.get("/api/tasks/{task_id}/checkins")
def list_task_checkins(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); ensure_project_access(conn, task["project_id"], request)
    rows = conn.execute("SELECT c.*,u.name user_name FROM task_checkins c JOIN users u ON u.id=c.user_id WHERE c.task_id=? ORDER BY c.id DESC", (task_id,)).fetchall(); conn.close(); return {"items": [dict(row) for row in rows]}


@app.get("/api/projects/{project_id}/checkins")
def list_project_checkins(
    project_id: int, request: Request, user_id: Optional[int] = None, task_id: Optional[int] = None,
    start_date: Optional[date] = None, end_date: Optional[date] = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    where = ["c.project_id=?"]; args: list[Any] = [project_id]
    if user_id is not None: where.append("c.user_id=?"); args.append(user_id)
    if task_id is not None: where.append("c.task_id=?"); args.append(task_id)
    if start_date: where.append("substr(c.created_at,1,10)>=?"); args.append(start_date.isoformat())
    if end_date: where.append("substr(c.created_at,1,10)<=?"); args.append(end_date.isoformat())
    condition = " AND ".join(where); total = conn.execute(f"SELECT COUNT(*) n FROM task_checkins c WHERE {condition}", args).fetchone()["n"]; offset, limit = pagination(page, page_size)
    rows = conn.execute(f"SELECT c.*,u.name user_name FROM task_checkins c JOIN users u ON u.id=c.user_id WHERE {condition} ORDER BY c.id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall(); conn.close()
    return {"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total}


@app.post("/api/tasks/{task_id}/review")
def review_task(task_id: int, payload: ReviewIn, response: Response, request: Request) -> dict[str, Any]:
    if abs(payload.quality * 10 - round(payload.quality * 10)) > 1e-8: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "quality", "message": "质量评分最多一位小数"}])
    conn = db(); task = task_row(conn, task_id); project, user, role = ensure_project_access(conn, task["project_id"], request, "owner"); ensure_writable(project); assert user is not None
    if task["status"] != "completed": conn.close(); fail(409, "CONFLICT", "只有已完成任务可以评价")
    if task["assignee_id"] == user["id"] and role != "owner": conn.close(); fail(403, "FORBIDDEN", "不能评价自己的任务")
    existing = conn.execute("SELECT id FROM task_reviews WHERE task_id=?", (task_id,)).fetchone(); stamp = now_iso()
    conn.execute("INSERT INTO task_review_history(task_id,reviewer_id,quality,comment,created_at) VALUES (?,?,?,?,?)", (task_id, user["id"], payload.quality, payload.comment, stamp))
    if existing:
        conn.execute("UPDATE task_reviews SET reviewer_id=?,quality=?,comment=?,updated_at=? WHERE task_id=?", (user["id"], payload.quality, payload.comment, stamp, task_id)); response.status_code = 200
    else:
        conn.execute("INSERT INTO task_reviews(task_id,reviewer_id,quality,comment,created_at,updated_at) VALUES (?,?,?,?,?,?)", (task_id, user["id"], payload.quality, payload.comment, stamp, stamp)); response.status_code = 201
    conn.execute("UPDATE tasks SET quality=?,updated_at=? WHERE id=?", (payload.quality, stamp, task_id)); conn.commit()
    row = conn.execute("SELECT r.*,u.name reviewer_name FROM task_reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.task_id=?", (task_id,)).fetchone(); conn.close(); return dict(row)


@app.get("/api/tasks/{task_id}/review")
def get_task_review(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); ensure_project_access(conn, task["project_id"], request)
    row = conn.execute("SELECT r.*,u.name reviewer_name FROM task_reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.task_id=?", (task_id,)).fetchone(); conn.close()
    if not row: fail(404, "NOT_FOUND", "任务尚未评价")
    return dict(row)


@app.get("/api/tasks/{task_id}/review/history")
def get_task_review_history(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); ensure_project_access(conn, task["project_id"], request)
    rows = conn.execute("SELECT h.*,u.name reviewer_name FROM task_review_history h JOIN users u ON u.id=h.reviewer_id WHERE h.task_id=? ORDER BY h.id DESC", (task_id,)).fetchall(); conn.close(); return {"items": [dict(row) for row in rows]}

@app.get("/api/projects/{project_id}/contributions")
def list_contributions(
    project_id: int, request: Request, user_id: Optional[int] = None, kind: Optional[str] = None,
    status: Optional[str] = None, source: Optional[str] = None, start_date: Optional[date] = None, end_date: Optional[date] = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    where = ["c.project_id=?", "c.deleted_at IS NULL"]; args: list[Any] = [project_id]
    for column, value in (("user_id", user_id), ("kind", kind), ("status", status), ("source", source)):
        if value is not None: where.append(f"c.{column}=?"); args.append(value)
    if start_date: where.append("substr(c.occurred_at,1,10)>=?"); args.append(start_date.isoformat())
    if end_date: where.append("substr(c.occurred_at,1,10)<=?"); args.append(end_date.isoformat())
    condition = " AND ".join(where); total = conn.execute(f"SELECT COUNT(*) n FROM contributions c WHERE {condition}", args).fetchone()["n"]; offset, limit = pagination(page, page_size)
    rows = conn.execute(f"SELECT c.*,u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE {condition} ORDER BY c.occurred_at DESC,c.id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall(); conn.close()
    return {"items": [as_contribution(row) for row in rows], "page": page, "page_size": page_size, "total": total}


@app.post("/api/projects/{project_id}/contributions", status_code=201)
def add_contribution(project_id: int, payload: ContributionIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    if not payload.title.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "title", "message": "贡献标题不能为空"}])
    conn = db(); project, user, role = ensure_project_access(conn, project_id, request, "member", allow_internal=request is None); ensure_writable(project)
    actor_id = user["id"] if user is not None else payload.user_id
    target_id = payload.user_id or actor_id
    if target_id is None: conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "user_id", "message": "成员不能为空"}])
    ensure_member(conn, project_id, target_id)
    if user is not None and role != "owner" and target_id != user["id"]: conn.close(); fail(403, "FORBIDDEN", "普通成员只能记录自己的贡献")
    stamp = now_iso(); occurred = iso_utc(payload.occurred_at) if payload.occurred_at else stamp
    cur = conn.execute(
        """INSERT INTO contributions(project_id,user_id,kind,title,description,quantity,metadata,evidence_url,status,source,occurred_at,created_at,updated_at,created_by)
        VALUES (?,?,?,?,?,?,?,?,'pending','manual',?,?,?,?)""",
        (project_id, target_id, payload.kind, payload.title, payload.description, payload.quantity, json.dumps(payload.metadata, ensure_ascii=False), payload.evidence_url, occurred, stamp, stamp, actor_id),
    ); conn.commit(); row = contribution_row(conn, cur.lastrowid); out = as_contribution(row); conn.close(); return out


@app.get("/api/contributions/{contribution_id}")
def get_contribution(contribution_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); ensure_project_access(conn, row["project_id"], request); out = as_contribution(row); conn.close(); return out


@app.patch("/api/contributions/{contribution_id}")
def update_contribution(contribution_id: int, payload: ContributionUpdate, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); project, user, role = ensure_project_access(conn, row["project_id"], request, "member"); ensure_writable(project); assert user is not None
    if row["status"] == "confirmed": conn.close(); fail(409, "CONFLICT", "已确认贡献不能修改")
    if role != "owner" and (row["created_by"] != user["id"] or row["status"] != "pending"): conn.close(); fail(403, "FORBIDDEN", "只能修改自己创建的待确认贡献")
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None and not data["title"].strip():
        conn.close()
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "title", "message": "贡献标题不能为空"}])
    if "occurred_at" in data and data["occurred_at"] is not None: data["occurred_at"] = iso_utc(data["occurred_at"])
    if data:
        data["updated_at"] = now_iso(); conn.execute(f"UPDATE contributions SET {','.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), contribution_id)); conn.commit()
    row = contribution_row(conn, contribution_id); out = as_contribution(row); conn.close(); return out


@app.post("/api/contributions/{contribution_id}/confirm")
def confirm_contribution(contribution_id: int, payload: NoteIn, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); project, user, _ = ensure_project_access(conn, row["project_id"], request, "owner"); ensure_writable(project); assert user is not None
    stamp = now_iso(); conn.execute("UPDATE contributions SET status='confirmed',confirmed_by=?,confirmed_at=?,confirmation_note=?,dispute_note=NULL,updated_at=? WHERE id=?", (user["id"], stamp, payload.note, stamp, contribution_id)); conn.commit(); conn.close()
    return {"id": contribution_id, "status": "confirmed", "confirmed_by": user["id"], "confirmed_at": stamp}


@app.post("/api/contributions/{contribution_id}/dispute")
def dispute_contribution(contribution_id: int, payload: NoteIn, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); project, _, _ = ensure_project_access(conn, row["project_id"], request, "owner"); ensure_writable(project)
    conn.execute("UPDATE contributions SET status='disputed',dispute_note=?,updated_at=? WHERE id=?", (payload.note, now_iso(), contribution_id)); conn.commit(); conn.close()
    return {"id": contribution_id, "status": "disputed", "dispute_note": payload.note}


@app.delete("/api/contributions/{contribution_id}", status_code=204)
def delete_contribution(contribution_id: int, request: Request) -> Response:
    conn = db(); row = contribution_row(conn, contribution_id); project, user, role = ensure_project_access(conn, row["project_id"], request, "member"); ensure_writable(project); assert user is not None
    if role != "owner" and (row["created_by"] != user["id"] or row["status"] != "pending"): conn.close(); fail(403, "FORBIDDEN", "只能删除自己创建的待确认贡献")
    conn.execute("UPDATE contributions SET deleted_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), contribution_id)); conn.commit(); conn.close(); return Response(status_code=204)


def internal_member_load(project_id: int) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    members = conn.execute("SELECT u.id user_id,u.name,u.max_concurrent_tasks FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id", (project_id,)).fetchall()
    result = []
    for member in members:
        tasks = conn.execute("SELECT id,COALESCE(estimated_hours,0) estimated_hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL AND status IN ('assigned','in_progress','paused','overdue')", (project_id, member["user_id"])).fetchall()
        current = len(tasks); maximum = max(1, member["max_concurrent_tasks"]); ratio = current / maximum
        level = "low" if ratio < .5 else ("normal" if ratio <= .8 else "high")
        result.append({"user_id": member["user_id"], "name": member["name"], "current_task_count": current, "max_concurrent_tasks": maximum, "remaining_capacity": max(0, maximum-current), "load_ratio": round(ratio, 2), "load_level": level, "estimated_hours": round(sum(task["estimated_hours"] for task in tasks), 2), "active_task_ids": [task["id"] for task in tasks]})
    conn.close(); return {"project_id": project_id, "generated_at": now_iso(), "members": result}


@app.get("/api/projects/{project_id}/members/load")
def members_load(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request); conn.close(); return internal_member_load(project_id)


def internal_recommendations(project_id: int, task_name: str, task_type: Optional[str], estimated_hours: float = 1, limit: int = 3) -> list[dict[str, Any]]:
    load = internal_member_load(project_id); conn = db(); result = []
    for member in load["members"]:
        if member["current_task_count"] >= member["max_concurrent_tasks"]: continue
        row = conn.execute("SELECT skills FROM users WHERE id=?", (member["user_id"],)).fetchone(); skills = json.loads(row["skills"] or "[]")
        needle = (task_type or task_name).lower(); skill_match = max([1.0 if skill.lower() in needle or needle in skill.lower() else 0.0 for skill in skills] or [0.0])
        quality = conn.execute("SELECT AVG(COALESCE(r.quality,t.quality)) q FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id WHERE t.project_id=? AND t.assignee_id=? AND (r.quality IS NOT NULL OR t.quality IS NOT NULL)", (project_id, member["user_id"])).fetchone()["q"] or 0
        ratios = [r["ratio"] for r in conn.execute("SELECT CASE WHEN actual_hours>0 THEN estimated_hours/actual_hours END ratio FROM tasks WHERE project_id=? AND assignee_id=? AND status='completed' AND estimated_hours IS NOT NULL AND actual_hours IS NOT NULL", (project_id, member["user_id"])).fetchall() if r["ratio"] is not None]
        efficiency = sum(ratios)/len(ratios) if ratios else 1.0
        capacity_score = 1 - member["load_ratio"]
        score = 100 * (.4*skill_match + .25*(quality/5) + .2*min(1.2, efficiency)/1.2 + .15*capacity_score)
        summary = f"技能匹配度{round(skill_match*100)}%，历史平均质量{round(quality,1) if quality else '暂无'}，当前负载{member['current_task_count']}/{member['max_concurrent_tasks']}。"
        result.append({"user_id": member["user_id"], "name": member["name"], "score": round(score, 1), "reasons": {"skill_match": round(skill_match, 2), "average_quality": round(quality, 2), "efficiency": round(efficiency, 2), "current_load": f"{member['current_task_count']}/{member['max_concurrent_tasks']}", "summary": summary}})
    conn.close(); return sorted(result, key=lambda item: item["score"], reverse=True)[:limit]


def recommendations(project_id: int, task_name: str, task_type: Optional[str], estimated_hours: float = 1) -> list[dict[str, Any]]:
    return internal_recommendations(project_id, task_name, task_type, estimated_hours)


@app.get("/api/projects/{project_id}/recommendations")
def get_recommendations(
    project_id: int, request: Request, task_id: Optional[int] = None, task_name: Optional[str] = None,
    task_type: Optional[str] = None, estimated_hours: float = Query(default=1, ge=0), limit: int = Query(default=3, ge=1, le=20),
) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    if (task_id is None) == (not task_name): conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "task_id", "message": "task_id 与 task_name 必须且只能提供一个"}])
    if task_id is not None:
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND project_id=? AND deleted_at IS NULL", (task_id, project_id)).fetchone()
        if not task: conn.close(); fail(404, "NOT_FOUND", "任务不存在")
        task_name = task["title"]; task_type = task["task_type"]; estimated_hours = task["estimated_hours"] if task["estimated_hours"] is not None else estimated_hours
    conn.close()
    task_obj = {"task_id": task_id, "task_name": task_name, "task_type": task_type, "estimated_hours": estimated_hours}
    return {"task": task_obj, "recommendations": internal_recommendations(project_id, task_name or "", task_type, estimated_hours, limit), "generated_at": now_iso()}


def internal_project_risks(project_id: int) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id); today = utc_today(); soon = today + timedelta(days=3); risks: list[dict[str, Any]] = []
    tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status!='completed'", (project_id,)).fetchall()
    for task in tasks:
        if task["status"] in ("overdue", "unfinished") or (task["due_date"] and task["due_date"] < today.isoformat()):
            risks.append({"type": "overdue_task", "level": "high", "message": f"任务「{task['title']}」已延期", "task_id": task["id"], "due_date": task["due_date"]})
        elif task["due_date"] and task["due_date"] <= soon.isoformat():
            risks.append({"type": "upcoming_deadline", "level": "medium", "message": f"任务「{task['title']}」临近截止", "task_id": task["id"], "due_date": task["due_date"]})
        if task["assignee_id"] is None:
            risks.append({"type": "unassigned_task", "level": "medium", "message": f"任务「{task['title']}」尚未分配", "task_id": task["id"], "due_date": task["due_date"]})
    for member in internal_member_load(project_id)["members"]:
        if member["load_level"] == "high": risks.append({"type": "high_member_load", "level": "medium", "message": f"{member['name']}当前负载为 {member['current_task_count']}/{member['max_concurrent_tasks']}", "user_id": member["user_id"], "current_task_count": member["current_task_count"], "max_concurrent_tasks": member["max_concurrent_tasks"]})
    last = conn.execute("SELECT MAX(at) at FROM task_logs l JOIN tasks t ON t.id=l.task_id WHERE t.project_id=?", (project_id,)).fetchone()["at"]
    if last:
        try:
            if datetime.fromisoformat(last.replace("Z", "+00:00")) < datetime.now(timezone.utc)-timedelta(days=7): risks.append({"type": "no_recent_activity", "level": "low", "message": "项目最近 7 天没有任务活动"})
        except ValueError: pass
    conn.close(); return {"project_id": project_id, "generated_at": now_iso(), "count": len(risks), "risks": risks}


@app.get("/api/projects/{project_id}/risks")
def project_risks(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request, allow_internal=request is None); conn.close(); return internal_project_risks(project_id)


def internal_project_report(project_id: int) -> dict[str, Any]:
    conn = db(); project = ensure_project(conn, project_id); stats = _project_stats(conn, project_id); members = conn.execute("SELECT u.id,u.name FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id", (project_id,)).fetchall(); items = []
    for member in members:
        task_stats = conn.execute("""SELECT COUNT(*) total,SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed,SUM(CASE WHEN status IN ('overdue','unfinished') THEN 1 ELSE 0 END) overdue,SUM(COALESCE(actual_hours,0)) hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL""", (project_id, member["id"])).fetchone()
        quality = conn.execute("SELECT AVG(COALESCE(r.quality,t.quality)) q FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id WHERE t.project_id=? AND t.assignee_id=? AND (r.quality IS NOT NULL OR t.quality IS NOT NULL)", (project_id, member["id"])).fetchone()["q"]
        contribs = conn.execute("SELECT kind,SUM(quantity) quantity FROM contributions WHERE project_id=? AND user_id=? AND status='confirmed' AND deleted_at IS NULL GROUP BY kind ORDER BY kind", (project_id, member["id"])).fetchall()
        items.append({"user_id": member["id"], "name": member["name"], "tasks_total": task_stats["total"] or 0, "tasks_completed": task_stats["completed"] or 0, "tasks_overdue": task_stats["overdue"] or 0, "average_quality": round(quality, 2) if quality is not None else None, "actual_hours": round(task_stats["hours"] or 0, 2), "contributions": [dict(row) for row in contribs]})
    conn.close(); return {"project_id": project_id, "project_name": project["name"], "generated_at": now_iso(), "overall": {"tasks_total": stats["task_count"], "tasks_completed": stats["completed_task_count"], "tasks_in_progress": stats["in_progress_task_count"], "tasks_overdue": stats["overdue_task_count"], "progress": stats["progress"]}, "members": items}


@app.get("/api/projects/{project_id}/report")
def project_report(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request, allow_internal=request is None); conn.close(); return internal_project_report(project_id)


@app.get("/api/projects/{project_id}/contribution-report")
def contribution_report(project_id: int, request: Request) -> dict[str, Any]:
    return project_report(project_id, request)

def _week_bounds(start_date: Optional[date], end_date: Optional[date]) -> tuple[date, date]:
    today = utc_today(); start = start_date or (today - timedelta(days=today.weekday())); end = end_date or (start + timedelta(days=6))
    if end < start: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "end_date", "message": "结束日期不能早于开始日期"}])
    return start, end


def internal_weekly_report(project_id: int, start: date, end: date) -> dict[str, Any]:
    conn = db(); project = ensure_project(conn, project_id); start_s, end_s = start.isoformat(), end.isoformat()
    total = conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL", (project_id,)).fetchone()["n"]
    completed = conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ?", (project_id, start_s, end_s)).fetchone()["n"]
    in_progress = conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status='in_progress'", (project_id,)).fetchone()["n"]
    overdue = conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status IN ('overdue','unfinished')", (project_id,)).fetchone()["n"]
    checkins = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(hours),0) hours FROM task_checkins WHERE project_id=? AND substr(created_at,1,10) BETWEEN ? AND ?", (project_id, start_s, end_s)).fetchone()
    contributions = conn.execute("SELECT COUNT(*) n FROM contributions WHERE project_id=? AND deleted_at IS NULL AND substr(occurred_at,1,10) BETWEEN ? AND ?", (project_id, start_s, end_s)).fetchone()["n"]
    task_hours = conn.execute("SELECT COALESCE(SUM(actual_hours),0) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND substr(updated_at,1,10) BETWEEN ? AND ?", (project_id, start_s, end_s)).fetchone()["n"]
    highlights = [row["title"] for row in conn.execute("SELECT title FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ? ORDER BY updated_at DESC LIMIT 5", (project_id, start_s, end_s)).fetchall()]
    risk_data = internal_project_risks(project_id); risks = [item["message"] for item in risk_data["risks"][:5]]
    next_actions: list[str] = []
    if any(item["type"] == "unassigned_task" for item in risk_data["risks"]): next_actions.append("优先分配未完成任务")
    if any(item["type"] == "overdue_task" for item in risk_data["risks"]): next_actions.append("为延期任务调整排期")
    if not next_actions: next_actions.append("按当前计划继续推进并及时打卡")
    members = []
    rows = conn.execute("SELECT u.id,u.name FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id", (project_id,)).fetchall()
    for member in rows:
        ms = conn.execute("""SELECT SUM(CASE WHEN status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ? THEN 1 ELSE 0 END) completed_tasks,SUM(CASE WHEN status IN ('assigned','in_progress','paused','overdue') THEN 1 ELSE 0 END) active_tasks,COALESCE(SUM(CASE WHEN substr(updated_at,1,10) BETWEEN ? AND ? THEN actual_hours ELSE 0 END),0) hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL""", (start_s, end_s, start_s, end_s, project_id, member["id"])).fetchone()
        ci = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(hours),0) hours FROM task_checkins WHERE project_id=? AND user_id=? AND substr(created_at,1,10) BETWEEN ? AND ?", (project_id, member["id"], start_s, end_s)).fetchone()
        members.append({"user_id": member["id"], "name": member["name"], "completed_tasks": ms["completed_tasks"] or 0, "active_tasks": ms["active_tasks"] or 0, "checkin_count": ci["n"], "actual_hours": round((ms["hours"] or 0) + (ci["hours"] or 0), 2)})
    conn.close()
    return {"project_id": project_id, "project_name": project["name"], "period": {"start_date": start_s, "end_date": end_s}, "summary": {"tasks_total": total, "tasks_completed": completed, "tasks_in_progress": in_progress, "tasks_overdue": overdue, "checkin_count": checkins["n"], "contribution_count": contributions, "actual_hours": round((task_hours or 0) + (checkins["hours"] or 0), 2)}, "highlights": highlights, "risks": risks, "next_actions": next_actions, "members": members, "generated_at": now_iso()}


def _weekly_markdown(data: dict[str, Any]) -> str:
    summary = data["summary"]
    lines = [f"# {data['project_name']} 周报", "", f"统计周期：{data['period']['start_date']} 至 {data['period']['end_date']}", "", "## 概览", f"- 任务总数：{summary['tasks_total']}", f"- 本周完成：{summary['tasks_completed']}", f"- 进行中：{summary['tasks_in_progress']}", f"- 延期：{summary['tasks_overdue']}", f"- 打卡次数：{summary['checkin_count']}", f"- 实际工时：{summary['actual_hours']}", "", "## 完成亮点"]
    lines.extend([f"- {item}" for item in data["highlights"]] or ["- 暂无已完成任务"])
    lines.extend(["", "## 风险", *([f"- {item}" for item in data["risks"]] or ["- 暂无明显风险"]), "", "## 下一步", *[f"- {item}" for item in data["next_actions"]], "", f"生成时间：{data['generated_at']}"])
    return "\n".join(lines)


@app.get("/api/projects/{project_id}/weekly-report")
def weekly_report(project_id: int, request: Request, start_date: Optional[date] = None, end_date: Optional[date] = None, format: Literal["json", "markdown"] = "json") -> Any:
    conn = db(); ensure_project_access(conn, project_id, request); conn.close(); start, end = _week_bounds(start_date, end_date); data = internal_weekly_report(project_id, start, end)
    if format == "markdown": return PlainTextResponse(_weekly_markdown(data), media_type="text/markdown; charset=utf-8")
    return data


def _report_markdown(data: dict[str, Any]) -> str:
    overall = data["overall"]
    lines = [f"# {data['project_name']} 项目报告", "", f"生成时间：{data['generated_at']}", "", "## 总览", f"- 任务总数：{overall['tasks_total']}", f"- 已完成：{overall['tasks_completed']}", f"- 进行中：{overall['tasks_in_progress']}", f"- 延期：{overall['tasks_overdue']}", f"- 进度：{overall['progress']}%", "", "## 成员数据（不排名）"]
    for member in data["members"]:
        lines.extend(["", f"### {member['name']}", f"- 任务：{member['tasks_completed']}/{member['tasks_total']}", f"- 延期任务：{member['tasks_overdue']}", f"- 平均质量：{member['average_quality'] if member['average_quality'] is not None else '暂无'}", f"- 实际工时：{member['actual_hours']}"])
    return "\n".join(lines) + "\n"


def _simple_pdf_bytes(title: str) -> bytes:
    safe = title.encode("ascii", "replace").decode("ascii").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 16 Tf 50 780 Td ({safe}) Tj ET".encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>", b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for index, obj in enumerate(objects, 1): offsets.append(len(out)); out.extend(f"{index} 0 obj\n".encode()); out.extend(obj); out.extend(b"\nendobj\n")
    xref = len(out); out.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()); return bytes(out)


@app.get("/api/projects/{project_id}/report/export")
def export_report(project_id: int, request: Request, format: Literal["markdown", "pdf"] = "markdown") -> Response:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request); conn.close(); data = internal_project_report(project_id)
    if format == "pdf":
        return Response(_simple_pdf_bytes(f"CollabLedger project report #{project_id}"), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="project-{project_id}-report.pdf"'})
    return PlainTextResponse(_report_markdown(data), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="project-{project_id}-report.md"'})


def internal_project_snapshot(project_id: int) -> dict[str, Any]:
    conn = db(); project = ensure_project(conn, project_id); detail = _project_detail(conn, project, None)
    members = list_members_internal(conn, project_id)
    tasks = [as_task(row) for row in conn.execute("SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.project_id=? AND t.deleted_at IS NULL ORDER BY t.id", (project_id,)).fetchall()]
    conn.close(); return {"project": detail, "members": members, "tasks": tasks, "report": internal_project_report(project_id), "risks": internal_project_risks(project_id)}


def list_members_internal(conn: sqlite3.Connection, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT m.user_id,u.name,m.role,u.skills,u.max_concurrent_tasks,u.status,m.joined_at FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY m.joined_at", (project_id,)).fetchall(); result = []
    for row in rows:
        item = dict(row); item["skills"] = json.loads(item["skills"] or "[]"); result.append(item)
    return result


def get_agent_runtime() -> AgentRuntime:
    return AgentRuntime(DB_PATH, AgentConfig.from_env())


@app.get("/api/agent/config")
def agent_config(request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request); conn.close(); return AgentConfig.from_env().public_dict()


@app.post("/api/projects/{project_id}/agent/chat")
def project_agent_chat(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request); conn.close()
    result = get_agent_runtime().run(project_id, payload.message, payload.session_id)
    facts = result.get("facts") or {}
    facts.setdefault("project_id", project_id)
    facts.setdefault("risk_count", (facts.get("risks") or {}).get("count", 0))
    result["facts"] = facts
    return {**result, "generated_at": now_iso()}


@app.get("/api/projects/{project_id}/agent/sessions")
def agent_sessions(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    # AgentMemory 会在首次调用时建表；尚未调用时返回空列表。
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_memory'").fetchone()
    if not exists: conn.close(); return {"items": []}
    rows = conn.execute("""SELECT session_id,COUNT(*) message_count,MAX(created_at) updated_at,
        (SELECT content FROM agent_memory a2 WHERE a2.project_id=a.project_id AND a2.session_id=a.session_id ORDER BY id DESC LIMIT 1) last_message
        FROM agent_memory a WHERE project_id=? GROUP BY session_id ORDER BY updated_at DESC""", (project_id,)).fetchall(); conn.close(); return {"items": [dict(row) for row in rows]}


@app.delete("/api/projects/{project_id}/agent/sessions/{session_id}", status_code=204)
def clear_agent_session(project_id: int, session_id: str, request: Request) -> Response:
    conn = db(); ensure_project_access(conn, project_id, request)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_memory'").fetchone(): conn.execute("DELETE FROM agent_memory WHERE project_id=? AND session_id=?", (project_id, session_id)); conn.commit()
    conn.close(); return Response(status_code=204)


@app.post("/api/projects/{project_id}/agent")
def agent(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    return project_agent_chat(project_id, payload, request)


@app.post("/api/agent/chat")
def agent_chat(project_id: int, payload: AgentIn, request: Request) -> dict[str, Any]:
    return project_agent_chat(project_id, payload, request)


FRONTEND_DIR = ROOT / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
