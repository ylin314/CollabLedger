from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.agent import AgentConfig, AgentRuntime


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
        """
    )
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


app = FastAPI(title="协作账本 API", version="0.1.0", description="面向小组作业的贡献留痕与智能协作 API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "collab-ledger"}


@app.post("/api/users", status_code=201)
def create_user(payload: UserIn) -> dict[str, Any]:
    conn = db(); cur = conn.execute("INSERT INTO users(name,email,skills,max_concurrent_tasks,status,created_at) VALUES (?,?,?,?,?,?)", (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, payload.status, now_iso())); conn.commit(); row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close(); return as_user(row)


@app.get("/api/users")
def list_users() -> list[dict[str, Any]]:
    conn = db(); rows = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall(); conn.close(); return [as_user(r) for r in rows]


@app.get("/api/users/{user_id}")
def get_user(user_id: int) -> dict[str, Any]:
    conn = db(); row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404, "用户不存在")
    return as_user(row)


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectIn) -> dict[str, Any]:
    conn = db()
    if payload.owner_id and not conn.execute("SELECT 1 FROM users WHERE id=?", (payload.owner_id,)).fetchone(): raise HTTPException(400, "owner_id 用户不存在")
    cur = conn.execute("INSERT INTO projects(name,project_type,description,start_date,end_date,owner_id,created_at) VALUES (?,?,?,?,?,?,?)", (payload.name, payload.project_type, payload.description, payload.start_date, payload.end_date, payload.owner_id, now_iso()))
    if payload.owner_id: conn.execute("INSERT OR IGNORE INTO memberships(project_id,user_id,role,joined_at) VALUES (?,?,?,?)", (cur.lastrowid, payload.owner_id, "owner", now_iso()))
    conn.commit(); row = conn.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close(); return dict(row)


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    conn = db(); rows = conn.execute("SELECT p.*, COUNT(DISTINCT m.user_id) member_count, COUNT(DISTINCT t.id) task_count FROM projects p LEFT JOIN memberships m ON p.id=m.project_id LEFT JOIN tasks t ON p.id=t.project_id GROUP BY p.id ORDER BY p.id DESC").fetchall(); conn.close(); return [dict(r) for r in rows]


@app.get("/api/projects/{project_id}")
def get_project(project_id: int) -> dict[str, Any]:
    conn = db(); row = ensure_project(conn, project_id); members = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall(); conn.close(); out = dict(row); out["members"] = [as_user(r) | {"role": r["role"]} for r in members]; out["tasks"] = [as_task(r) for r in tasks]; return out


@app.post("/api/projects/{project_id}/members", status_code=201)
def add_member(project_id: int, payload: MemberIn) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    user_id = payload.user_id
    if user_id is None:
        if not payload.name: raise HTTPException(422, "需要 user_id 或 name")
        cur = conn.execute("INSERT INTO users(name,email,skills,max_concurrent_tasks,status,created_at) VALUES (?,?,?,?,?,?)", (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, "offline", now_iso())); user_id = cur.lastrowid
    elif not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone(): raise HTTPException(404, "用户不存在")
    conn.execute("INSERT OR REPLACE INTO memberships(project_id,user_id,role,joined_at) VALUES (?,?,?,?)", (project_id, user_id, payload.role, now_iso())); conn.commit(); row = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=? AND u.id=?", (project_id, user_id)).fetchone(); conn.close(); return as_user(row) | {"role": row["role"]}


@app.get("/api/projects/{project_id}/members")
def list_members(project_id: int) -> list[dict[str, Any]]:
    conn = db(); ensure_project(conn, project_id); rows = conn.execute("SELECT u.*, m.role FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); conn.close(); return [as_user(r) | {"role": r["role"]} for r in rows]


@app.post("/api/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: int, payload: TaskIn) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    if payload.assignee_id: ensure_member(conn, project_id, payload.assignee_id)
    status = (payload.status if payload.status != "unassigned" else "assigned") if payload.assignee_id else "unassigned"
    ts = now_iso(); cur = conn.execute("INSERT INTO tasks(project_id,title,description,assignee_id,status,due_date,estimated_hours,task_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (project_id, payload.title, payload.description, payload.assignee_id, status, payload.due_date, payload.estimated_hours, payload.task_type, ts, ts));
    if payload.assignee_id: conn.execute("INSERT INTO task_logs(task_id,user_id,action,at) VALUES (?,?,?,?)", (cur.lastrowid, payload.assignee_id, "assigned", ts))
    conn.commit(); row = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close(); return as_task(row)


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: int, status: Optional[str] = None) -> list[dict[str, Any]]:
    conn = db(); ensure_project(conn, project_id); sql = "SELECT t.*, u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.project_id=?"; args: list[Any] = [project_id]
    if status: sql += " AND t.status=?"; args.append(status)
    sql += " ORDER BY t.id DESC"; rows = conn.execute(sql, args).fetchall(); conn.close(); return [dict(r) for r in rows]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    conn = db(); row = conn.execute("SELECT t.*, u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.id=?", (task_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404, "任务不存在")
    return dict(row)


@app.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: int, user_id: int, note: Optional[str] = None) -> dict[str, Any]:
    return update_task(task_id, TaskUpdate(assignee_id=user_id, status="assigned", user_id=user_id, note=note))


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate) -> dict[str, Any]:
    conn = db(); row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: raise HTTPException(404, "任务不存在")
    data = payload.model_dump(exclude_none=True); user_id = data.pop("user_id", None); note = data.pop("note", None)
    if "assignee_id" in data and data["assignee_id"]: ensure_member(conn, row["project_id"], data["assignee_id"])
    if data:
        data["updated_at"] = now_iso(); sets = ",".join(f"{k}=?" for k in data); conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*data.values(), task_id))
    if "status" in data or "assignee_id" in data or note:
        conn.execute("INSERT INTO task_logs(task_id,user_id,action,note,at) VALUES (?,?,?,?,?)", (task_id, user_id or row["assignee_id"], data.get("status") or "updated", note, now_iso()))
    conn.commit(); out = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(); conn.close(); return as_task(out)


@app.get("/api/tasks/{task_id}/logs")
def task_logs(task_id: int) -> list[dict[str, Any]]:
    conn = db()
    if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone(): raise HTTPException(404, "任务不存在")
    rows = conn.execute("SELECT l.*, u.name user_name FROM task_logs l LEFT JOIN users u ON u.id=l.user_id WHERE task_id=? ORDER BY id", (task_id,)).fetchall(); conn.close(); return [dict(r) for r in rows]


@app.post("/api/tasks/{task_id}/{action}")
def task_action(task_id: int, action: str, user_id: Optional[int] = None, note: Optional[str] = None) -> dict[str, Any]:
    """任务生命周期快捷操作：start/pause/resume/complete/overdue/unfinished。"""
    transitions = {"start": "in_progress", "pause": "paused", "resume": "in_progress", "complete": "completed", "overdue": "overdue", "delay": "overdue", "unfinished": "unfinished"}
    if action not in transitions:
        raise HTTPException(404, "不支持的任务操作")
    conn = db(); row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: raise HTTPException(404, "任务不存在")
    actor = user_id or row["assignee_id"]
    if actor: ensure_member(conn, row["project_id"], actor)
    status = transitions[action]; conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_iso(), task_id)); conn.execute("INSERT INTO task_logs(task_id,user_id,action,note,at) VALUES (?,?,?,?,?)", (task_id, actor, action, note, now_iso())); conn.commit(); out = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(); conn.close(); return as_task(out)


@app.post("/api/projects/{project_id}/contributions", status_code=201)
def add_contribution(project_id: int, payload: ContributionIn) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id); ensure_member(conn, project_id, payload.user_id); cur = conn.execute("INSERT INTO contributions(project_id,user_id,kind,title,description,quantity,metadata,created_at) VALUES (?,?,?,?,?,?,?,?)", (project_id, payload.user_id, payload.kind, payload.title, payload.description, payload.quantity, json.dumps(payload.metadata, ensure_ascii=False), now_iso())); conn.commit(); row = conn.execute("SELECT c.*,u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.id=?", (cur.lastrowid,)).fetchone(); conn.close(); out = dict(row); out["metadata"] = json.loads(out["metadata"]); return out


@app.get("/api/projects/{project_id}/contributions")
def list_contributions(project_id: int, user_id: Optional[int] = None) -> list[dict[str, Any]]:
    conn = db(); ensure_project(conn, project_id); sql = "SELECT c.*, u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.project_id=?"; args: list[Any] = [project_id]
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
def get_recommendations(project_id: int, task_name: str = Query(...), task_type: Optional[str] = None, estimated_hours: float = 1) -> dict[str, Any]:
    return {"task": {"name": task_name, "type": task_type, "estimated_hours": estimated_hours}, "recommendations": recommendations(project_id, task_name, task_type, estimated_hours)}


@app.get("/api/projects/{project_id}/report")
def project_report(project_id: int) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id); members = conn.execute("SELECT u.* FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.project_id=?", (project_id,)).fetchall(); report = []
    for m in members:
        tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? AND assignee_id=?", (project_id, m["id"])).fetchall(); contribs = conn.execute("SELECT kind, SUM(quantity) quantity FROM contributions WHERE project_id=? AND user_id=? GROUP BY kind", (project_id, m["id"])).fetchall(); completed = [t for t in tasks if t["status"] == "completed"]; qualities = [t["quality"] for t in completed if t["quality"] is not None]; report.append({"user_id": m["id"], "name": m["name"], "tasks_total": len(tasks), "tasks_completed": len(completed), "tasks_overdue": sum(1 for t in tasks if t["status"] in ("overdue", "unfinished")), "average_quality": round(sum(qualities) / len(qualities), 2) if qualities else None, "actual_hours": round(sum((t["actual_hours"] or 0) for t in completed), 2), "contributions": [dict(c) for c in contribs]})
    overall = {"tasks": conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=?", (project_id,)).fetchone()["n"], "completed": conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=? AND status='completed'", (project_id,)).fetchone()["n"]}; conn.close(); return {"project_id": project_id, "generated_at": now_iso(), "overall": overall, "members": report}


@app.get("/api/projects/{project_id}/contribution-report")
def contribution_report(project_id: int) -> dict[str, Any]:
    return project_report(project_id)


@app.get("/api/projects/{project_id}/risks")
def project_risks(project_id: int) -> dict[str, Any]:
    tasks = list_tasks(project_id)
    risky = [t for t in tasks if t["status"] in ("overdue", "unfinished") or (t.get("due_date") and t["status"] != "completed" and t["due_date"] < datetime.now().date().isoformat())]
    return {"project_id": project_id, "count": len(risky), "risks": [{"task_id": t["id"], "title": t["title"], "status": t["status"], "due_date": t.get("due_date")} for t in risky]}


@app.get("/api/projects/{project_id}/weekly-report")
def weekly_report(project_id: int) -> dict[str, Any]:
    return project_report(project_id)


def get_agent_runtime() -> AgentRuntime:
    # 每次请求读取环境配置，便于容器通过环境变量更新后重启即可生效。
    return AgentRuntime(DB_PATH, AgentConfig.from_env())


@app.get("/api/agent/config")
def agent_config() -> dict[str, Any]:
    """返回脱敏后的 Agent 配置，不返回完整 API Key。"""
    return AgentConfig.from_env().public_dict()


@app.post("/api/projects/{project_id}/agent")
def agent(project_id: int, payload: AgentIn) -> dict[str, Any]:
    result = get_agent_runtime().run(project_id, payload.message, payload.session_id)
    return {"project_id": project_id, "message": payload.message, **result}


@app.post("/api/agent/chat")
def agent_chat(project_id: int, payload: AgentIn) -> dict[str, Any]:
    return agent(project_id, payload)


# 若存在构建后的前端目录，直接托管；API 路由优先匹配。
FRONTEND_DIR = ROOT / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
