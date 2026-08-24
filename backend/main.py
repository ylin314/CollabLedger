from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent import AgentConfig, AgentRuntime
from backend.auth import create_session, current_user, hash_password, revoke_session, verify_password


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("COLLAB_DB", ROOT / "collab.db"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
        """
    )
    # 对已有 collab.db 做向前兼容迁移。
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "password_hash" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    conn.commit()
    conn.close()


# 初始化也在导入时执行，便于 CLI、测试客户端和 ASGI 启动器一致工作。
init_db()


class UserIn(BaseModel):
    name: str = Field(min_length=1)
    email: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)
    status: str = "offline"
    password: Optional[str] = Field(default=None, min_length=8)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    skills: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)


class LoginIn(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    project_type: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class InvitationIn(BaseModel):
    email: Optional[str] = None
    role: str = Field(default="member", pattern="^(member|viewer)$")
    expires_days: int = Field(default=7, ge=1, le=30)


class AcceptInvitationIn(BaseModel):
    token: Optional[str] = Field(default=None, min_length=8)
    invite_code: Optional[str] = Field(default=None, min_length=4)


class RoleUpdate(BaseModel):
    role: str = Field(pattern="^(member|viewer)$")


class WorkLogIn(BaseModel):
    work_date: Optional[str] = None
    hours: float = Field(default=0, ge=0, le=24)
    note: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None


class QualityReviewIn(BaseModel):
    task_id: Optional[int] = None
    reviewee_id: int
    score: float = Field(ge=0, le=5)
    comment: Optional[str] = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1)
    project_type: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    owner_id: Optional[int] = None


class MemberIn(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    role: str = "member"
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)


class TaskIn(BaseModel):
    title: str = Field(min_length=1)
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    status: str = "unassigned"
    due_date: Optional[str] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    task_type: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee_id: Optional[int] = None
    status: Optional[str] = None
    due_date: Optional[str] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    actual_hours: Optional[float] = Field(default=None, ge=0)
    quality: Optional[float] = Field(default=None, ge=0, le=5)
    task_type: Optional[str] = None
    user_id: Optional[int] = None
    note: Optional[str] = None


class ContributionIn(BaseModel):
    user_id: int
    kind: str = "other"
    title: Optional[str] = None
    description: Optional[str] = None
    quantity: float = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentIn(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = Field(default="default", min_length=1, max_length=100)


def as_user(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["skills"] = json.loads(out.pop("skills") or "[]")
    return out


def public_user(row: sqlite3.Row) -> dict[str, Any]:
    out = as_user(row)
    out.pop("password_hash", None)
    out.pop("token_hash", None)
    return out


def as_task(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def ensure_project(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return row


def ensure_member(conn: sqlite3.Connection, project_id: int, user_id: int) -> None:
    if not conn.execute("SELECT 1 FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone():
        raise HTTPException(400, "用户不是该项目成员")


ROLE_LEVEL = {"viewer": 1, "member": 2, "owner": 3}


def project_role(conn: sqlite3.Connection, project_id: int, user_id: int) -> Optional[str]:
    row = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
    return row["role"] if row else None


def ensure_project_access(
    conn: sqlite3.Connection,
    project_id: int,
    request: Optional[Request],
    minimum_role: str = "viewer",
    required: bool = False,
) -> Optional[sqlite3.Row]:
    project = ensure_project(conn, project_id)
    user = current_user(conn, request, required=required)
    if not user:
        return project
    role = project_role(conn, project_id, user["id"])
    if not role or ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum_role]:
        raise HTTPException(status_code=403, detail="没有该项目的访问权限")
    return project


def ensure_role(conn: sqlite3.Connection, project_id: int, request: Optional[Request], minimum_role: str) -> Optional[sqlite3.Row]:
    return ensure_project_access(conn, project_id, request, minimum_role, required=True)


app = FastAPI(title="协作账本 API", version="0.1.0", description="面向小组作业的贡献留痕与智能协作 API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "collab-ledger"}


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterIn) -> dict[str, Any]:
    conn = db()
    if conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (payload.email,)).fetchone():
        conn.close()
        raise HTTPException(status_code=409, detail="邮箱已注册")
    cur = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at) VALUES (?,?,?,?,?,?,?)",
        (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, "offline", hash_password(payload.password), now_iso()),
    )
    token, expires_at = create_session(conn, cur.lastrowid)
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return {"user": public_user(row), "access_token": token, "token_type": "bearer", "expires_at": expires_at}


@app.post("/api/auth/login")
def login(payload: LoginIn) -> dict[str, Any]:
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (payload.email,)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token, expires_at = create_session(conn, row["id"])
    conn.commit()
    conn.close()
    return {"user": public_user(row), "access_token": token, "token_type": "bearer", "expires_at": expires_at}


@app.post("/api/auth/logout")
def logout(request: Request) -> dict[str, Any]:
    conn = db()
    revoked = revoke_session(conn, request)
    conn.commit()
    conn.close()
    return {"ok": True, "revoked": revoked}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    conn = db()
    row = current_user(conn, request, required=True)
    projects = conn.execute(
        """SELECT p.id,p.name,p.project_type,p.description,p.start_date,p.end_date,p.owner_id,p.created_at,m.role
           FROM projects p JOIN memberships m ON m.project_id=p.id WHERE m.user_id=? ORDER BY p.id DESC""",
        (row["id"],),
    ).fetchall()
    conn.close()
    return {"user": public_user(row), "projects": [dict(p) for p in projects]}


@app.post("/api/auth/accept-invitation")
def accept_invitation(payload: AcceptInvitationIn, request: Request) -> dict[str, Any]:
    conn = db()
    user = current_user(conn, request, required=True)
    if not payload.token and not payload.invite_code:
        conn.close()
        raise HTTPException(status_code=422, detail="需要 token 或 invite_code")
    token_hash = __import__("hashlib").sha256(payload.token.encode()).hexdigest() if payload.token else ""
    invitation = conn.execute(
        "SELECT * FROM project_invitations WHERE (invite_hash=? OR invite_code=?) AND accepted_at IS NULL AND expires_at>?",
        (token_hash, (payload.invite_code or "").upper(), now_iso()),
    ).fetchone()
    if not invitation:
        conn.close()
        raise HTTPException(status_code=404, detail="邀请不存在、已使用或已过期")
    if invitation["email"] and invitation["email"].lower() != (user["email"] or "").lower():
        conn.close()
        raise HTTPException(status_code=403, detail="该邀请限定了其他邮箱")
    conn.execute(
        "INSERT OR REPLACE INTO memberships(project_id,user_id,role,joined_at) VALUES (?,?,?,?)",
        (invitation["project_id"], user["id"], invitation["role"], now_iso()),
    )
    conn.execute("UPDATE project_invitations SET accepted_at=? WHERE id=?", (now_iso(), invitation["id"]))
    conn.commit()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (invitation["project_id"],)).fetchone()
    conn.close()
    return {"project": dict(project), "role": invitation["role"], "user_id": user["id"]}


@app.post("/api/users", status_code=201)
def create_user(payload: UserIn) -> dict[str, Any]:
    conn = db()
    if payload.email and conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (payload.email,)).fetchone():
        conn.close()
        raise HTTPException(status_code=409, detail="邮箱已注册")
    password_hash = hash_password(payload.password) if payload.password else None
    cur = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at) VALUES (?,?,?,?,?,?,?)",
        (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, payload.status, password_hash, now_iso()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return public_user(row)


@app.get("/api/users")
def list_users() -> list[dict[str, Any]]:
    conn = db(); rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall(); conn.close(); return [public_user(r) for r in rows]


@app.get("/api/users/{user_id}")
def get_user(user_id: int) -> dict[str, Any]:
    conn = db(); row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404, "用户不存在")
    return public_user(row)


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db()
    authenticated = current_user(conn, request, required=False)
    owner_id = authenticated["id"] if authenticated else payload.owner_id
    if owner_id and not conn.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone():
        conn.close()
        raise HTTPException(400, "owner_id 用户不存在")
    cur = conn.execute("INSERT INTO projects(name,project_type,description,start_date,end_date,owner_id,created_at) VALUES (?,?,?,?,?,?,?)", (payload.name, payload.project_type, payload.description, payload.start_date, payload.end_date, owner_id, now_iso()))
    if owner_id:
        conn.execute("INSERT OR IGNORE INTO memberships(project_id,user_id,role,joined_at) VALUES (?,?,?,?)", (cur.lastrowid, owner_id, "owner", now_iso()))
    conn.commit(); row = conn.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close(); return dict(row)


@app.get("/api/projects")
def list_projects(request: Request) -> list[dict[str, Any]]:
    conn = db()
    user = current_user(conn, request, required=False)
    query = "SELECT p.*, COUNT(DISTINCT m.user_id) member_count, COUNT(DISTINCT t.id) task_count FROM projects p LEFT JOIN memberships m ON p.id=m.project_id LEFT JOIN tasks t ON p.id=t.project_id"
    args: list[Any] = []
    if user:
        query += " JOIN memberships mine ON mine.project_id=p.id AND mine.user_id=?"
        args.append(user["id"])
    query += " GROUP BY p.id ORDER BY p.id DESC"
    rows = conn.execute(query, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); row = ensure_project_access(conn, project_id, request); members = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall(); conn.close(); out = dict(row); out["members"] = [public_user(r) | {"role": r["role"]} for r in members]; out["tasks"] = [as_task(r) for r in tasks]; return out


@app.patch("/api/projects/{project_id}")
def update_project(project_id: int, payload: ProjectUpdate, request: Request) -> dict[str, Any]:
    conn = db()
    ensure_role(conn, project_id, request, "owner") if request.headers.get("authorization") else ensure_project(conn, project_id)
    data = payload.model_dump(exclude_none=True)
    if data:
        sets = ",".join(f"{key}=?" for key in data)
        conn.execute(f"UPDATE projects SET {sets} WHERE id=?", (*data.values(), project_id))
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    return dict(row)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, request: Request) -> dict[str, Any]:
    conn = db()
    ensure_role(conn, project_id, request, "owner") if request.headers.get("authorization") else ensure_project(conn, project_id)
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "project_id": project_id}


@app.post("/api/projects/{project_id}/members", status_code=201)
def add_member(project_id: int, payload: MemberIn, request: Request) -> dict[str, Any]:
    conn = db()
    ensure_role(conn, project_id, request, "owner") if request.headers.get("authorization") else ensure_project(conn, project_id)
    if payload.role not in ("member", "viewer"):
        conn.close()
        raise HTTPException(422, "成员角色只能是 member 或 viewer")
    user_id = payload.user_id
    if user_id is None:
        if not payload.name: raise HTTPException(422, "需要 user_id 或 name")
        cur = conn.execute("INSERT INTO users(name,email,skills,max_concurrent_tasks,status,created_at) VALUES (?,?,?,?,?,?)", (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, "offline", now_iso())); user_id = cur.lastrowid
    elif not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone(): raise HTTPException(404, "用户不存在")
    conn.execute("INSERT OR REPLACE INTO memberships(project_id,user_id,role,joined_at) VALUES (?,?,?,?)", (project_id, user_id, payload.role, now_iso())); conn.commit(); row = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=? AND u.id=?", (project_id, user_id)).fetchone(); conn.close(); return public_user(row) | {"role": row["role"]}


@app.get("/api/projects/{project_id}/members")
def list_members(project_id: int, request: Request) -> list[dict[str, Any]]:
    conn = db(); ensure_project_access(conn, project_id, request); rows = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); conn.close(); return [public_user(r) | {"role": r["role"]} for r in rows]


@app.patch("/api/projects/{project_id}/members/{user_id}")
def update_member_role(project_id: int, user_id: int, payload: RoleUpdate, request: Request) -> dict[str, Any]:
    conn = db()
    ensure_role(conn, project_id, request, "owner") if request.headers.get("authorization") else ensure_project(conn, project_id)
    membership = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
    if not membership:
        conn.close()
        raise HTTPException(404, "成员不存在")
    if membership["role"] == "owner":
        conn.close()
        raise HTTPException(400, "不能修改项目所有者角色")
    conn.execute("UPDATE memberships SET role=? WHERE project_id=? AND user_id=?", (payload.role, project_id, user_id))
    conn.commit()
    row = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=? AND u.id=?", (project_id, user_id)).fetchone()
    conn.close()
    return public_user(row) | {"role": row["role"]}


@app.delete("/api/projects/{project_id}/members/{user_id}")
def remove_member(project_id: int, user_id: int, request: Request) -> dict[str, Any]:
    conn = db()
    owner = ensure_role(conn, project_id, request, "owner") if request.headers.get("authorization") else None
    membership = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
    if not membership:
        conn.close()
        raise HTTPException(404, "成员不存在")
    if membership["role"] == "owner":
        conn.close()
        raise HTTPException(400, "不能移除项目所有者")
    conn.execute("DELETE FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "project_id": project_id, "user_id": user_id}


@app.post("/api/projects/{project_id}/invitations", status_code=201)
def create_invitation(project_id: int, payload: InvitationIn, request: Request) -> dict[str, Any]:
    conn = db()
    inviter = ensure_role(conn, project_id, request, "owner")
    inviter_id = inviter["owner_id"] if inviter else None
    raw_token = secrets.token_urlsafe(32)
    invite_code = secrets.token_hex(4).upper()
    expires = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    expires = (expires + timedelta(days=payload.expires_days)).isoformat()
    conn.execute(
        "INSERT INTO project_invitations(project_id,inviter_id,invite_hash,invite_code,email,role,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (project_id, inviter_id, __import__("hashlib").sha256(raw_token.encode()).hexdigest(), invite_code, payload.email, payload.role, expires, now_iso()),
    )
    conn.commit()
    invitation_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.close()
    return {"id": invitation_id, "project_id": project_id, "email": payload.email, "role": payload.role, "invite_code": invite_code, "token": raw_token, "expires_at": expires}


@app.get("/api/projects/{project_id}/invitations")
def list_invitations(project_id: int, request: Request) -> list[dict[str, Any]]:
    conn = db(); ensure_role(conn, project_id, request, "owner")
    rows = conn.execute("SELECT id,project_id,email,role,invite_code,expires_at,accepted_at,created_at FROM project_invitations WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: int, payload: TaskIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db()
    if request is not None and request.headers.get("authorization"):
        ensure_role(conn, project_id, request, "member")
    else:
        ensure_project(conn, project_id)
    if payload.assignee_id: ensure_member(conn, project_id, payload.assignee_id)
    status = (payload.status if payload.status != "unassigned" else "assigned") if payload.assignee_id else "unassigned"
    ts = now_iso(); cur = conn.execute("INSERT INTO tasks(project_id,title,description,assignee_id,status,due_date,estimated_hours,task_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (project_id, payload.title, payload.description, payload.assignee_id, status, payload.due_date, payload.estimated_hours, payload.task_type, ts, ts));
    if payload.assignee_id: conn.execute("INSERT INTO task_logs(task_id,user_id,action,at) VALUES (?,?,?,?)", (cur.lastrowid, payload.assignee_id, "assigned", ts))
    conn.commit(); row = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close(); return as_task(row)


@app.post("/api/projects/{project_id}/work-logs", status_code=201)
def create_work_log(project_id: int, payload: WorkLogIn, request: Request, user_id: Optional[int] = Query(default=None)) -> dict[str, Any]:
    conn = db()
    user = current_user(conn, request, required=False)
    actor_id = user["id"] if user else user_id
    if actor_id is None:
        conn.close()
        raise HTTPException(status_code=401, detail="请登录或提供 user_id")
    ensure_member(conn, project_id, actor_id)
    if user and ROLE_LEVEL.get(project_role(conn, project_id, user["id"]) or "", 0) < ROLE_LEVEL["member"]:
        conn.close()
        raise HTTPException(status_code=403, detail="查看者不能提交工作日志")
    if user and user["id"] != actor_id:
        conn.close()
        raise HTTPException(status_code=403, detail="只能记录自己的工作日志")
    work_date = payload.work_date or datetime.now(timezone.utc).date().isoformat()
    timestamp = now_iso()
    conn.execute(
        """INSERT INTO work_logs(project_id,user_id,work_date,hours,note,check_in,check_out,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id,user_id,work_date) DO UPDATE SET hours=excluded.hours,note=excluded.note,check_in=excluded.check_in,check_out=excluded.check_out,updated_at=excluded.updated_at""",
        (project_id, actor_id, work_date, payload.hours, payload.note, payload.check_in, payload.check_out, timestamp, timestamp),
    )
    conn.commit()
    row = conn.execute("SELECT w.*,u.name user_name FROM work_logs w JOIN users u ON u.id=w.user_id WHERE w.project_id=? AND w.user_id=? AND w.work_date=?", (project_id, actor_id, work_date)).fetchone()
    conn.close()
    return dict(row)


@app.post("/api/projects/{project_id}/work-logs/check-in", status_code=201)
def check_in(project_id: int, request: Request, user_id: Optional[int] = Query(default=None), work_date: Optional[str] = None, note: Optional[str] = None) -> dict[str, Any]:
    date = work_date or datetime.now(timezone.utc).date().isoformat()
    return create_work_log(project_id, WorkLogIn(work_date=date, check_in=now_iso(), note=note), request, user_id)


@app.post("/api/projects/{project_id}/work-logs/check-out", status_code=201)
def check_out(project_id: int, request: Request, user_id: Optional[int] = Query(default=None), work_date: Optional[str] = None, hours: float = Query(default=0, ge=0, le=24), note: Optional[str] = None) -> dict[str, Any]:
    date = work_date or datetime.now(timezone.utc).date().isoformat()
    conn = db()
    user = current_user(conn, request, required=False)
    actor_id = user["id"] if user else user_id
    if actor_id is None:
        conn.close()
        raise HTTPException(status_code=401, detail="请登录或提供 user_id")
    existing = conn.execute("SELECT * FROM work_logs WHERE project_id=? AND user_id=? AND work_date=?", (project_id, actor_id, date)).fetchone()
    conn.close()
    if existing:
        return create_work_log(project_id, WorkLogIn(work_date=date, hours=hours or existing["hours"], note=note or existing["note"], check_in=existing["check_in"], check_out=now_iso()), request, user_id)
    return create_work_log(project_id, WorkLogIn(work_date=date, hours=hours, note=note, check_out=now_iso()), request, user_id)


@app.get("/api/projects/{project_id}/work-logs")
def list_work_logs(project_id: int, request: Request, user_id: Optional[int] = Query(default=None), work_date: Optional[str] = None) -> list[dict[str, Any]]:
    conn = db()
    ensure_project_access(conn, project_id, request)
    sql = "SELECT w.*,u.name user_name FROM work_logs w JOIN users u ON u.id=w.user_id WHERE w.project_id=?"
    args: list[Any] = [project_id]
    if user_id:
        sql += " AND w.user_id=?"; args.append(user_id)
    if work_date:
        sql += " AND w.work_date=?"; args.append(work_date)
    rows = conn.execute(sql + " ORDER BY w.work_date DESC,w.id DESC", args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.delete("/api/projects/{project_id}/work-logs/{log_id}")
def delete_work_log(project_id: int, log_id: int, request: Request) -> dict[str, Any]:
    conn = db()
    user = current_user(conn, request, required=False)
    row = conn.execute("SELECT * FROM work_logs WHERE id=? AND project_id=?", (log_id, project_id)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "工作日志不存在")
    if user:
        role = project_role(conn, project_id, user["id"])
        if not role or (row["user_id"] != user["id"] and ROLE_LEVEL.get(role, 0) < ROLE_LEVEL["owner"]):
            conn.close()
            raise HTTPException(403, "没有删除该工作日志的权限")
    conn.execute("DELETE FROM work_logs WHERE id=?", (log_id,)); conn.commit(); conn.close()
    return {"ok": True, "log_id": log_id}


@app.post("/api/projects/{project_id}/quality-reviews", status_code=201)
def create_quality_review(project_id: int, payload: QualityReviewIn, request: Request, reviewer_id: Optional[int] = Query(default=None)) -> dict[str, Any]:
    conn = db()
    user = current_user(conn, request, required=False)
    actor_id = user["id"] if user else reviewer_id
    if actor_id is None:
        conn.close()
        raise HTTPException(status_code=401, detail="请登录或提供 reviewer_id")
    ensure_member(conn, project_id, actor_id)
    if user and ROLE_LEVEL.get(project_role(conn, project_id, user["id"]) or "", 0) < ROLE_LEVEL["member"]:
        conn.close()
        raise HTTPException(status_code=403, detail="查看者不能提交质量评价")
    ensure_member(conn, project_id, payload.reviewee_id)
    if user and actor_id == payload.reviewee_id:
        conn.close()
        raise HTTPException(status_code=400, detail="不能评价自己")
    if payload.task_id:
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND project_id=?", (payload.task_id, project_id)).fetchone()
        if not task:
            conn.close()
            raise HTTPException(404, "任务不存在或不属于该项目")
        if task["assignee_id"] and task["assignee_id"] != payload.reviewee_id:
            conn.close()
            raise HTTPException(400, "任务负责人不是被评价成员")
    timestamp = now_iso()
    conn.execute(
        """INSERT INTO quality_reviews(project_id,task_id,reviewer_id,reviewee_id,score,comment,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id,task_id,reviewer_id,reviewee_id) DO UPDATE SET score=excluded.score,comment=excluded.comment,updated_at=excluded.updated_at""",
        (project_id, payload.task_id, actor_id, payload.reviewee_id, payload.score, payload.comment, timestamp, timestamp),
    )
    conn.commit()
    row = conn.execute("""SELECT q.*,r.name reviewer_name,e.name reviewee_name,t.title task_title
                          FROM quality_reviews q JOIN users r ON r.id=q.reviewer_id JOIN users e ON e.id=q.reviewee_id
                          LEFT JOIN tasks t ON t.id=q.task_id WHERE q.project_id=? AND q.reviewer_id=? AND q.reviewee_id=? AND (q.task_id IS ? OR q.task_id=?)""", (project_id, actor_id, payload.reviewee_id, payload.task_id, payload.task_id)).fetchone()
    conn.close()
    return dict(row)


@app.get("/api/projects/{project_id}/quality-reviews")
def list_quality_reviews(project_id: int, request: Request, reviewee_id: Optional[int] = None, task_id: Optional[int] = None) -> list[dict[str, Any]]:
    conn = db(); ensure_project_access(conn, project_id, request)
    sql = """SELECT q.*,r.name reviewer_name,e.name reviewee_name,t.title task_title
             FROM quality_reviews q JOIN users r ON r.id=q.reviewer_id JOIN users e ON e.id=q.reviewee_id LEFT JOIN tasks t ON t.id=q.task_id WHERE q.project_id=?"""
    args: list[Any] = [project_id]
    if reviewee_id:
        sql += " AND q.reviewee_id=?"; args.append(reviewee_id)
    if task_id:
        sql += " AND q.task_id=?"; args.append(task_id)
    rows = conn.execute(sql + " ORDER BY q.updated_at DESC", args).fetchall(); conn.close()
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_id}/quality-summary")
def quality_summary(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request)
    rows = conn.execute("""SELECT q.reviewee_id,u.name,COUNT(*) review_count,ROUND(AVG(q.score),2) average_score
                          FROM quality_reviews q JOIN users u ON u.id=q.reviewee_id WHERE q.project_id=? GROUP BY q.reviewee_id,u.name ORDER BY average_score DESC""", (project_id,)).fetchall()
    conn.close()
    return {"project_id": project_id, "members": [dict(r) for r in rows]}


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: int, status: Optional[str] = None, request: Request = None) -> list[dict[str, Any]]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request); sql = "SELECT t.*, u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.project_id=?"; args: list[Any] = [project_id]
    if status: sql += " AND t.status=?"; args.append(status)
    sql += " ORDER BY t.id DESC"; rows = conn.execute(sql, args).fetchall(); conn.close(); return [dict(r) for r in rows]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); row = conn.execute("SELECT t.*, u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.id=?", (task_id,)).fetchone()
    if not row: raise HTTPException(404, "任务不存在")
    ensure_project_access(conn, row["project_id"], request)
    conn.close()
    return dict(row)


@app.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: int, user_id: int, note: Optional[str] = None, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    return update_task(task_id, TaskUpdate(assignee_id=user_id, status="assigned", user_id=user_id, note=note), request)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: raise HTTPException(404, "任务不存在")
    if request is not None and request.headers.get("authorization"):
        ensure_role(conn, row["project_id"], request, "member")
    data = payload.model_dump(exclude_none=True); user_id = data.pop("user_id", None); note = data.pop("note", None)
    if "assignee_id" in data and data["assignee_id"]: ensure_member(conn, row["project_id"], data["assignee_id"])
    if data:
        data["updated_at"] = now_iso(); sets = ",".join(f"{k}=?" for k in data); conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*data.values(), task_id))
    if "status" in data or "assignee_id" in data or note:
        conn.execute("INSERT INTO task_logs(task_id,user_id,action,note,at) VALUES (?,?,?,?,?)", (task_id, user_id or row["assignee_id"], data.get("status") or "updated", note, now_iso()))
    conn.commit(); out = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(); conn.close(); return as_task(out)


@app.get("/api/tasks/{task_id}/logs")
def task_logs(task_id: int, request: Request = None) -> list[dict[str, Any]]:  # type: ignore[assignment]
    conn = db()
    task = conn.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task: raise HTTPException(404, "任务不存在")
    ensure_project_access(conn, task["project_id"], request)
    rows = conn.execute("SELECT l.*, u.name user_name FROM task_logs l LEFT JOIN users u ON u.id=l.user_id WHERE task_id=? ORDER BY id", (task_id,)).fetchall(); conn.close(); return [dict(r) for r in rows]


@app.post("/api/tasks/{task_id}/{action}")
def task_action(task_id: int, action: str, user_id: Optional[int] = None, note: Optional[str] = None, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    """任务生命周期快捷操作：start/pause/resume/complete/overdue/unfinished。"""
    transitions = {"start": "in_progress", "pause": "paused", "resume": "in_progress", "complete": "completed", "overdue": "overdue", "delay": "overdue", "unfinished": "unfinished"}
    if action not in transitions:
        raise HTTPException(404, "不支持的任务操作")
    conn = db(); row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: raise HTTPException(404, "任务不存在")
    if request is not None and request.headers.get("authorization"):
        ensure_role(conn, row["project_id"], request, "member")
    actor = user_id or row["assignee_id"]
    if actor: ensure_member(conn, row["project_id"], actor)
    status = transitions[action]; conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_iso(), task_id)); conn.execute("INSERT INTO task_logs(task_id,user_id,action,note,at) VALUES (?,?,?,?,?)", (task_id, actor, action, note, now_iso())); conn.commit(); out = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(); conn.close(); return as_task(out)


@app.post("/api/projects/{project_id}/contributions", status_code=201)
def add_contribution(project_id: int, payload: ContributionIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request)
    actor = current_user(conn, request, required=False)
    if actor and ROLE_LEVEL.get(project_role(conn, project_id, actor["id"]) or "", 0) < ROLE_LEVEL["member"]:
        conn.close(); raise HTTPException(403, "查看者不能提交贡献记录")
    if actor and actor["id"] != payload.user_id:
        conn.close(); raise HTTPException(403, "只能记录自己的贡献")
    ensure_member(conn, project_id, payload.user_id); cur = conn.execute("INSERT INTO contributions(project_id,user_id,kind,title,description,quantity,metadata,created_at) VALUES (?,?,?,?,?,?,?,?)", (project_id, payload.user_id, payload.kind, payload.title, payload.description, payload.quantity, json.dumps(payload.metadata, ensure_ascii=False), now_iso())); conn.commit(); row = conn.execute("SELECT c.*,u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.id=?", (cur.lastrowid,)).fetchone(); conn.close(); out = dict(row); out["metadata"] = json.loads(out["metadata"]); return out


@app.get("/api/projects/{project_id}/contributions")
def list_contributions(project_id: int, user_id: Optional[int] = None, request: Request = None) -> list[dict[str, Any]]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request); sql = "SELECT c.*, u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.project_id=?"; args: list[Any] = [project_id]
    if user_id: sql += " AND c.user_id=?"; args.append(user_id)
    rows = conn.execute(sql + " ORDER BY c.id DESC", args).fetchall(); conn.close(); out = []
    for r in rows:
        x = dict(r); x["metadata"] = json.loads(x["metadata"] or "{}"); out.append(x)
    return out


def recommendations(project_id: int, task_name: str, task_type: Optional[str], estimated_hours: float = 1) -> list[dict[str, Any]]:
    conn = db(); ensure_project(conn, project_id)
    members = conn.execute("SELECT u.* FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); result = []
    for m in members:
        active = conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND assignee_id=? AND status IN ('assigned','in_progress','paused','unassigned')", (project_id, m["id"])).fetchone()["n"]
        if active >= m["max_concurrent_tasks"]: continue
        hist = conn.execute("SELECT quality, estimated_hours, actual_hours, task_type FROM tasks WHERE assignee_id=? AND status='completed'", (m["id"],)).fetchall()
        skills = json.loads(m["skills"] or "[]"); text = f"{task_name} {(task_type or '')}".lower(); skill_match = sum(1 for s in skills if s.lower() in text) / max(1, len(skills))
        quality = sum((h["quality"] or 3) for h in hist) / len(hist) if hist else 3
        ratios = [h["estimated_hours"] / h["actual_hours"] for h in hist if h["estimated_hours"] and h["actual_hours"]]
        efficiency = min(1.3, max(.7, sum(ratios) / len(ratios))) if ratios else 1
        score = 100 * (0.4 * min(1, skill_match * 1.5) + 0.3 * quality / 5 + 0.2 * min(1.3, efficiency) / 1.3 + 0.1 * (1 - active / max(1, m["max_concurrent_tasks"])))
        result.append({"user_id": m["id"], "name": m["name"], "score": round(score, 1), "reasons": {"skills": skills, "skill_match": round(skill_match, 2), "average_quality": round(quality, 2), "efficiency": round(efficiency, 2), "current_load": f"{active}/{m['max_concurrent_tasks']}"}})
    conn.close(); return sorted(result, key=lambda x: x["score"], reverse=True)


@app.get("/api/projects/{project_id}/recommendations")
def get_recommendations(project_id: int, task_name: str = Query(...), task_type: Optional[str] = None, estimated_hours: float = 1, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request); conn.close()
    return {"task": {"name": task_name, "type": task_type, "estimated_hours": estimated_hours}, "recommendations": recommendations(project_id, task_name, task_type, estimated_hours)}


@app.get("/api/projects/{project_id}/report")
def project_report(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request); members = conn.execute("SELECT u.* FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); report = []
    for m in members:
        tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? AND assignee_id=?", (project_id, m["id"])).fetchall(); contribs = conn.execute("SELECT kind, SUM(quantity) quantity FROM contributions WHERE project_id=? AND user_id=? GROUP BY kind", (project_id, m["id"])).fetchall(); completed = [t for t in tasks if t["status"] == "completed"]; qualities = [t["quality"] for t in completed if t["quality"] is not None]; report.append({"user_id": m["id"], "name": m["name"], "tasks_total": len(tasks), "tasks_completed": len(completed), "tasks_overdue": sum(1 for t in tasks if t["status"] in ("overdue", "unfinished")), "average_quality": round(sum(qualities) / len(qualities), 2) if qualities else None, "actual_hours": round(sum((t["actual_hours"] or 0) for t in completed), 2), "contributions": [dict(c) for c in contribs]})
    overall = {"tasks": conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=?", (project_id,)).fetchone()["n"], "completed": conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND status='completed'", (project_id,)).fetchone()["n"]}; conn.close(); return {"project_id": project_id, "generated_at": now_iso(), "overall": overall, "members": report}


@app.get("/api/projects/{project_id}/contribution-report")
def contribution_report(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    return project_report(project_id, request)


@app.get("/api/projects/{project_id}/risks")
def project_risks(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    tasks = list_tasks(project_id, request=request)
    risky = [t for t in tasks if t["status"] in ("overdue", "unfinished") or (t.get("due_date") and t["status"] != "completed" and t["due_date"] < datetime.now().date().isoformat())]
    return {"project_id": project_id, "count": len(risky), "risks": [{"task_id": t["id"], "title": t["title"], "status": t["status"], "due_date": t.get("due_date")} for t in risky]}


@app.get("/api/projects/{project_id}/weekly-report")
def weekly_report(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    return project_report(project_id, request)


def get_agent_runtime() -> AgentRuntime:
    # 每次请求读取环境配置，便于容器通过环境变量更新后重启即可生效。
    return AgentRuntime(DB_PATH, AgentConfig.from_env())


@app.get("/api/agent/config")
def agent_config() -> dict[str, Any]:
    """返回脱敏后的 Agent 配置，不返回完整 API Key。"""
    return AgentConfig.from_env().public_dict()


@app.post("/api/projects/{project_id}/agent")
def agent(project_id: int, payload: AgentIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request); conn.close()
    result = get_agent_runtime().run(project_id, payload.message, payload.session_id)
    return {"project_id": project_id, "message": payload.message, **result}


@app.post("/api/agent/chat")
def agent_chat(project_id: int, payload: AgentIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    return agent(project_id, payload, request)


# 若存在构建后的前端目录，直接托管；API 路由优先匹配。
FRONTEND_DIR = ROOT / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
