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

@router.get("/api/projects/{project_id}/contributions")
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


@router.post("/api/projects/{project_id}/contributions", status_code=201)
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


@router.get("/api/contributions/{contribution_id}")
def get_contribution(contribution_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); ensure_project_access(conn, row["project_id"], request); out = as_contribution(row); conn.close(); return out


@router.patch("/api/contributions/{contribution_id}")
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


@router.post("/api/contributions/{contribution_id}/confirm")
def confirm_contribution(contribution_id: int, payload: NoteIn, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); project, user, _ = ensure_project_access(conn, row["project_id"], request, "owner"); ensure_writable(project); assert user is not None
    stamp = now_iso(); conn.execute("UPDATE contributions SET status='confirmed',confirmed_by=?,confirmed_at=?,confirmation_note=?,dispute_note=NULL,updated_at=? WHERE id=?", (user["id"], stamp, payload.note, stamp, contribution_id)); conn.commit(); conn.close()
    return {"id": contribution_id, "status": "confirmed", "confirmed_by": user["id"], "confirmed_at": stamp}


@router.post("/api/contributions/{contribution_id}/dispute")
def dispute_contribution(contribution_id: int, payload: NoteIn, request: Request) -> dict[str, Any]:
    conn = db(); row = contribution_row(conn, contribution_id); project, _, _ = ensure_project_access(conn, row["project_id"], request, "owner"); ensure_writable(project)
    conn.execute("UPDATE contributions SET status='disputed',dispute_note=?,updated_at=? WHERE id=?", (payload.note, now_iso(), contribution_id)); conn.commit(); conn.close()
    return {"id": contribution_id, "status": "disputed", "dispute_note": payload.note}


@router.delete("/api/contributions/{contribution_id}", status_code=204)
def delete_contribution(contribution_id: int, request: Request) -> Response:
    conn = db(); row = contribution_row(conn, contribution_id); project, user, role = ensure_project_access(conn, row["project_id"], request, "member"); ensure_writable(project); assert user is not None
    if role != "owner" and (row["created_by"] != user["id"] or row["status"] != "pending"): conn.close(); fail(403, "FORBIDDEN", "只能删除自己创建的待确认贡献")
    conn.execute("UPDATE contributions SET deleted_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), contribution_id)); conn.commit(); conn.close(); return Response(status_code=204)

__all__ = ['list_contributions', 'add_contribution', 'get_contribution', 'update_contribution', 'confirm_contribution', 'dispute_contribution', 'delete_contribution']
