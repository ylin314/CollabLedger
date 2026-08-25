from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse, Response

from backend.auth import COOKIE_NAME, create_session, hash_password, iso_utc, revoke_session, verify_password
from backend.core.context import *
from backend.schemas import *

router = APIRouter()

@router.get("/api/projects")
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


@router.post("/api/projects", status_code=201)
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


@router.get("/api/projects/{project_id}")
def get_project(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db()
    project, _, role = ensure_project_access(conn, project_id, request, allow_internal=request is None)
    out = _project_detail(conn, project, role)
    conn.close()
    return out


@router.patch("/api/projects/{project_id}")
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


@router.post("/api/projects/{project_id}/archive")
def archive_project(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request, "owner")
    if project["status"] == "archived": conn.close(); fail(409, "CONFLICT", "项目已归档")
    stamp = now_iso(); conn.execute("UPDATE projects SET status='archived',archived_at=?,updated_at=? WHERE id=?", (stamp, stamp, project_id)); conn.commit(); conn.close()
    return {"id": project_id, "status": "archived", "archived_at": stamp}


@router.post("/api/projects/{project_id}/restore")
def restore_project(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); project, _, role = ensure_project_access(conn, project_id, request, "owner")
    if project["status"] != "archived": conn.close(); fail(409, "CONFLICT", "项目未归档")
    stamp = now_iso(); conn.execute("UPDATE projects SET status='active',archived_at=NULL,updated_at=? WHERE id=?", (stamp, project_id)); conn.commit()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone(); out = _project_detail(conn, project, role); conn.close(); return out


@router.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: int, request: Request) -> Response:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    conn.execute("UPDATE projects SET deleted_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), project_id)); conn.commit(); conn.close()
    return Response(status_code=204)


@router.post("/api/projects/{project_id}/members", status_code=201)
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


@router.get("/api/projects/{project_id}/members")
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


@router.patch("/api/projects/{project_id}/members/{user_id:int}")
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


@router.delete("/api/projects/{project_id}/members/{user_id:int}", status_code=204)
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


@router.post("/api/projects/{project_id}/invitations", status_code=201)
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


@router.get("/api/projects/{project_id}/invitations")
def list_invitations(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request, "owner")
    rows = conn.execute("SELECT * FROM project_invitations WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall(); conn.close()
    return {"items": [_invitation_dict(row) for row in rows]}


@router.post("/api/invitations/{invitation_id}/revoke")
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


@router.get("/api/invitations/{code}")
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


@router.post("/api/invitations/{code}/accept")
def accept_invitation_code(code: str, request: Request) -> dict[str, Any]:
    return _accept_code(code, request)


@router.post("/api/auth/accept-invitation")
def accept_invitation(payload: AcceptInvitationIn, request: Request) -> dict[str, Any]:
    code = payload.invite_code or payload.token
    if not code: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "code", "message": "邀请码不能为空"}])
    return _accept_code(code, request)

__all__ = ['list_projects', 'create_project', 'get_project', 'update_project', 'archive_project', 'restore_project', 'delete_project', 'add_member', 'list_members', 'update_member_role', 'remove_member', '_invitation_dict', 'create_invitation', 'list_invitations', 'revoke_invitation', '_load_invitation', '_invitation_valid', 'get_invitation', '_accept_code', 'accept_invitation_code', 'accept_invitation']
