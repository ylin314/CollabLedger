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

def _task_log(conn: sqlite3.Connection, task_id: int, user_id: Optional[int], action: str, from_status: Optional[str], to_status: Optional[str], note: Optional[str]) -> dict[str, Any]:
    stamp = now_iso()
    cur = conn.execute("INSERT INTO task_logs(task_id,user_id,action,from_status,to_status,note,at) VALUES (?,?,?,?,?,?,?)", (task_id, user_id, action, from_status, to_status, note, stamp))
    return {"id": cur.lastrowid, "action": action, "note": note, "user_id": user_id, "at": stamp}


@router.get("/api/projects/{project_id}/tasks")
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
    rows = conn.execute(f"SELECT t.*,u.name assignee_name,r.name reviewer_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id LEFT JOIN users r ON r.id=t.reviewer_id WHERE {condition} ORDER BY {sort_sql} {order.upper()},t.id {order.upper()} LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    task_ids = [row["id"] for row in rows]
    participant_map: dict[int, list[dict[str, Any]]] = {task_id: [] for task_id in task_ids}
    if task_ids:
        marks = ",".join("?" for _ in task_ids)
        participant_rows = conn.execute(f"SELECT tp.task_id,tp.user_id,tp.role,u.name,u.email FROM task_participants tp JOIN users u ON u.id=tp.user_id WHERE tp.task_id IN ({marks}) AND tp.status='active'", task_ids).fetchall()
        for participant in participant_rows:
            participant_map[participant["task_id"]].append({"id": participant["user_id"], "name": participant["name"], "email": participant["email"], "role": participant["role"]})
    out = []
    for row in rows:
        item = as_task(row)
        item["participants"] = participant_map.get(row["id"], [])
        item["participant_ids"] = [person["id"] for person in item["participants"]]
        out.append(item)
    conn.close()
    return {"items": out, "page": page, "page_size": page_size, "total": total}


@router.post("/api/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: int, payload: TaskIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    if not payload.title.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "title", "message": "任务标题不能为空"}])
    conn = db()
    project, user, role = ensure_project_access(conn, project_id, request, "member", allow_internal=request is None)
    ensure_writable(project)
    if payload.status and payload.status not in TASK_STATUSES: conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "status", "message": "任务状态不正确"}])
    if payload.assignee_id is not None: ensure_member(conn, project_id, payload.assignee_id)
    if payload.reviewer_id is not None: ensure_member(conn, project_id, payload.reviewer_id)
    for participant_id in payload.participant_ids: ensure_member(conn, project_id, participant_id)
    status = payload.status if request is None and payload.status else ("assigned" if payload.assignee_id is not None else "unassigned")
    actor_id = user["id"] if user is not None else (payload.owner_id if hasattr(payload, "owner_id") else None)
    stamp = now_iso()
    cur = conn.execute(
        """INSERT INTO tasks(project_id,title,description,assignee_id,status,due_date,estimated_hours,actual_hours,quality,task_type,priority,created_by,reviewer_id,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,?)""",
        (project_id, payload.title, payload.description, payload.assignee_id, status, payload.due_date.isoformat() if payload.due_date else None, payload.estimated_hours, payload.task_type, payload.priority, actor_id, payload.reviewer_id, stamp, stamp),
    )
    _task_log(conn, cur.lastrowid, actor_id, "created", None, status, None); conn.commit()
    sync_task_participants(conn, cur.lastrowid, payload.participant_ids, payload.assignee_id)
    conn.commit(); row = task_row(conn, cur.lastrowid); out = as_task(row, conn); conn.close(); return out


@router.get("/api/tasks/{task_id}")
def get_task(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = task_row(conn, task_id); ensure_project_access(conn, row["project_id"], request); out = as_task(row, conn); conn.close(); return out


@router.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); row = task_row(conn, task_id)
    project, user, role = ensure_project_access(conn, row["project_id"], request, "member", allow_internal=request is None); ensure_writable(project)
    raw = payload.model_dump(exclude_unset=True)
    if "title" in raw and raw["title"] is not None and not raw["title"].strip():
        conn.close()
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "title", "message": "任务标题不能为空"}])
    raw.pop("user_id", None); note = raw.pop("note", None); participant_ids = raw.pop("participant_ids", None)
    if participant_ids is not None and user is not None and role != "owner":
        conn.close(); fail(403, "FORBIDDEN", "只有 owner 可以修改任务参与者")
    current_participant_ids = task_participant_ids(conn, task_id)
    participant_changed = participant_ids is not None and set(participant_ids) != set(current_participant_ids)
    if request is not None and ("status" in raw or "quality" in raw):
        conn.close()
        field = "status" if "status" in raw else "quality"
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": field, "message": "请使用专用状态或评价接口"}])
    for key, value in list(raw.items()):
        if isinstance(value, date): raw[key] = value.isoformat()
    if "reviewer_id" in raw:
        is_creator = user is not None and row["created_by"] == user["id"]
        if user is not None and role != "owner" and not is_creator:
            conn.close(); fail(403, "FORBIDDEN", "只有 owner 或任务创建者可以修改评审人")
        if raw["reviewer_id"] is not None: ensure_member(conn, row["project_id"], raw["reviewer_id"])
    if user is not None and role != "owner":
        is_creator = row["created_by"] == user["id"]
        non_reviewer_fields = set(raw) - ({"reviewer_id"} if is_creator else set())
        if non_reviewer_fields:
            if row["assignee_id"] != user["id"]: conn.close(); fail(403, "FORBIDDEN", "只能更新自己负责的任务")
            forbidden = non_reviewer_fields - {"actual_hours"}
            if forbidden: conn.close(); fail(403, "FORBIDDEN", "普通成员只能更新任务执行字段")
    if "status" in raw and raw["status"] not in TASK_STATUSES: conn.close(); fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "status", "message": "任务状态不正确"}])
    if "assignee_id" in raw and raw["assignee_id"] is not None: ensure_member(conn, row["project_id"], raw["assignee_id"])
    if participant_ids is not None:
        for participant_id in participant_ids: ensure_member(conn, row["project_id"], participant_id)
    if "assignee_id" in raw and "status" not in raw:
        raw["status"] = "assigned" if raw["assignee_id"] is not None else "unassigned"
    before_status = row["status"]
    changed = {key: value for key, value in raw.items() if value != row[key]}
    participant_note = "更新参与者" if participant_changed else None
    if changed:
        changed["updated_at"] = now_iso(); conn.execute(f"UPDATE tasks SET {','.join(f'{k}=?' for k in changed)} WHERE id=?", (*changed.values(), task_id))
        actor = user["id"] if user is not None else payload.user_id
        action = "assigned" if "assignee_id" in changed else "updated"
        field_note = "更新字段：" + "、".join(k for k in changed if k != "updated_at")
        log_note = "；".join(item for item in (note, field_note, participant_note) if item)
        _task_log(conn, task_id, actor, action, before_status, changed.get("status", before_status), log_note)
    elif note or participant_note:
        _task_log(conn, task_id, user["id"] if user is not None else payload.user_id, "participants_updated" if participant_note else "updated", before_status, before_status, "；".join(item for item in (note, participant_note) if item))
    if participant_ids is not None: sync_task_participants(conn, task_id, participant_ids, raw.get("assignee_id", row["assignee_id"]))
    conn.commit(); row = task_row(conn, task_id); out = as_task(row, conn); conn.close(); return out


def assign_task_in_connection(conn, task_id: int, assignee_id: int, actor_id: Optional[int], note: Optional[str]) -> dict[str, Any]:
    """在调用方事务内完成指派，供推荐采纳和 HTTP 指派共同复用。"""
    row = task_row(conn, task_id)
    ensure_member(conn, row["project_id"], assignee_id)
    before = row["status"]
    stamp = now_iso()
    conn.execute(
        "UPDATE tasks SET assignee_id=?,status='assigned',updated_at=? WHERE id=?",
        (assignee_id, stamp, task_id),
    )
    sync_task_participants(conn, task_id, task_participant_ids(conn, task_id), assignee_id)
    log = _task_log(conn, task_id, actor_id if actor_id is not None else assignee_id, "assigned", before, "assigned", note)
    updated = task_row(conn, task_id)
    result = as_task(updated, conn)
    result["log"] = log
    return result

@router.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: int, payload: AssignIn = None, request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    # Query 参数兼容旧客户端；契约客户端使用 JSON body。
    assignee_id = payload.assignee_id if payload is not None else user_id
    assignment_note = payload.note if payload is not None else note
    if assignee_id is None: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "assignee_id", "message": "负责人不能为空"}])
    conn = db()
    row = task_row(conn, task_id)
    project, user, _ = ensure_project_access(conn, row["project_id"], request, "member", allow_internal=request is None)
    ensure_writable(project)
    out = assign_task_in_connection(conn, task_id, assignee_id, user["id"] if user is not None else assignee_id, assignment_note)
    conn.commit()
    conn.close()
    return out

def _task_action(task_id: int, action: str, payload: TaskActionIn, request: Request, legacy_user_id: Optional[int] = None) -> dict[str, Any]:
    targets = {"start": "in_progress", "pause": "paused", "resume": "in_progress", "complete": "completed", "overdue": "overdue", "unfinished": "unfinished"}
    allowed = {
        "start": {"assigned"}, "pause": {"in_progress"}, "resume": {"paused"},
        "complete": {"assigned", "in_progress", "paused", "overdue"}, "overdue": {"assigned", "in_progress", "paused"},
        "unfinished": {"unassigned", "assigned", "in_progress", "paused", "overdue"},
    }
    conn = db(); row = task_row(conn, task_id); project, user, role = ensure_project_access(conn, row["project_id"], request, "member", allow_internal=request is None); ensure_writable(project)
    actor_id = user["id"] if user is not None else legacy_user_id
    if user is not None and role != "owner" and user["id"] not in task_participant_ids(conn, task_id): conn.close(); fail(403, "FORBIDDEN", "只有项目 owner 或任务参与者可以执行该操作")
    if action in ("start", "pause", "resume", "complete") and row["assignee_id"] is None: conn.close(); fail(409, "CONFLICT", "任务尚未指派负责人")
    if row["status"] not in allowed[action]: conn.close(); fail(409, "CONFLICT", f"当前状态不能执行{action}操作")
    target = targets[action]; stamp = now_iso(); values: list[Any] = [target, stamp]
    sql = "UPDATE tasks SET status=?,updated_at=?"
    if action == "complete" and payload.actual_hours is not None: sql += ",actual_hours=?"; values.append(payload.actual_hours)
    sql += " WHERE id=?"; values.append(task_id); conn.execute(sql, values)
    log = _task_log(conn, task_id, actor_id, action, row["status"], target, payload.note); conn.commit(); conn.close()
    return {"id": task_id, "status": target, "updated_at": stamp, "log": log}


@router.post("/api/tasks/{task_id}/start")
def start_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note, actual_hours=payload.actual_hours)
    return _task_action(task_id, "start", payload, request, user_id)


@router.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "pause", payload, request, user_id)


@router.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "resume", payload, request, user_id)


@router.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note, actual_hours=payload.actual_hours)
    return _task_action(task_id, "complete", payload, request, user_id)


@router.post("/api/tasks/{task_id}/overdue")
def overdue_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "overdue", payload, request, user_id)


@router.post("/api/tasks/{task_id}/unfinished")
def unfinished_task(task_id: int, payload: TaskActionIn = TaskActionIn(), request: Request = None, user_id: Optional[int] = Query(default=None), note: Optional[str] = Query(default=None)) -> dict[str, Any]:  # type: ignore[assignment]
    if note and not payload.note: payload = TaskActionIn(note=note)
    return _task_action(task_id, "unfinished", payload, request, user_id)


@router.get("/api/tasks/{task_id}/logs")
def task_logs(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); row = task_row(conn, task_id); ensure_project_access(conn, row["project_id"], request)
    rows = conn.execute("SELECT l.id,l.task_id,l.user_id,u.name user_name,l.action,l.from_status,l.to_status,l.note,l.at FROM task_logs l LEFT JOIN users u ON u.id=l.user_id WHERE l.task_id=? ORDER BY l.id", (task_id,)).fetchall(); conn.close(); return {"items": [dict(item) for item in rows]}


@router.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, request: Request) -> Response:
    conn = db(); row = task_row(conn, task_id); project, user, role = ensure_project_access(conn, row["project_id"], request, "viewer"); ensure_writable(project)
    if user is not None and role != "owner" and row["created_by"] != user["id"]:
        conn.close(); fail(403, "FORBIDDEN", "只有 owner 或任务创建人可以删除任务")
    conn.execute("UPDATE tasks SET deleted_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), task_id)); conn.commit(); conn.close(); return Response(status_code=204)


@router.post("/api/tasks/{task_id}/checkins", status_code=201)
def create_checkin(task_id: int, payload: CheckinIn, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); project, user, role = ensure_project_access(conn, task["project_id"], request, "member"); ensure_writable(project); assert user is not None
    if role != "owner" and user["id"] not in task_participant_ids(conn, task_id): conn.close(); fail(403, "FORBIDDEN", "只有项目 owner 或任务参与者可以打卡")
    stamp = now_iso(); cur = conn.execute("INSERT INTO task_checkins(task_id,project_id,user_id,content,hours,blockers,created_at) VALUES (?,?,?,?,?,?,?)", (task_id, task["project_id"], user["id"], payload.content, payload.hours, payload.blockers, stamp)); conn.commit()
    row = conn.execute("SELECT c.*,u.name user_name FROM task_checkins c JOIN users u ON u.id=c.user_id WHERE c.id=?", (cur.lastrowid,)).fetchone(); conn.close(); return dict(row)


@router.get("/api/tasks/{task_id}/checkins")
def list_task_checkins(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); ensure_project_access(conn, task["project_id"], request)
    rows = conn.execute("SELECT c.*,u.name user_name FROM task_checkins c JOIN users u ON u.id=c.user_id WHERE c.task_id=? ORDER BY c.id DESC", (task_id,)).fetchall(); conn.close(); return {"items": [dict(row) for row in rows]}


@router.get("/api/projects/{project_id}/checkins")
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


@router.post("/api/tasks/{task_id}/review")
def review_task(task_id: int, payload: ReviewIn, response: Response, request: Request) -> dict[str, Any]:
    if abs(payload.quality * 10 - round(payload.quality * 10)) > 1e-8: fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "quality", "message": "质量评分最多一位小数"}])
    conn = db(); task = task_row(conn, task_id); project, user, role = ensure_project_access(conn, task["project_id"], request, "viewer"); ensure_writable(project); assert user is not None
    is_reviewer = task["reviewer_id"] is not None and task["reviewer_id"] == user["id"]
    if role != "owner" and not is_reviewer: conn.close(); fail(403, "FORBIDDEN", "只有 owner 或该任务的评审人可以评价")
    if task["status"] != "completed": conn.close(); fail(409, "CONFLICT", "只有已完成任务可以评价")
    if task["assignee_id"] == user["id"] and role != "owner": conn.close(); fail(403, "FORBIDDEN", "不能评价自己的任务")
    existing = conn.execute("SELECT id FROM task_reviews WHERE task_id=?", (task_id,)).fetchone(); stamp = now_iso()
    conn.execute("INSERT INTO task_review_history(task_id,reviewer_id,quality,comment,created_at,updated_at) VALUES (?,?,?,?,?,?)", (task_id, user["id"], payload.quality, payload.comment, stamp, stamp))
    if existing:
        conn.execute("UPDATE task_reviews SET reviewer_id=?,quality=?,comment=?,updated_at=? WHERE task_id=?", (user["id"], payload.quality, payload.comment, stamp, task_id)); response.status_code = 200
    else:
        conn.execute("INSERT INTO task_reviews(task_id,reviewer_id,quality,comment,created_at,updated_at) VALUES (?,?,?,?,?,?)", (task_id, user["id"], payload.quality, payload.comment, stamp, stamp)); response.status_code = 201
    conn.execute("UPDATE tasks SET quality=?,updated_at=? WHERE id=?", (payload.quality, stamp, task_id)); conn.commit()
    row = conn.execute("SELECT r.*,u.name reviewer_name FROM task_reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.task_id=?", (task_id,)).fetchone(); conn.close(); return dict(row)


@router.get("/api/tasks/{task_id}/review")
def get_task_review(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); ensure_project_access(conn, task["project_id"], request)
    row = conn.execute("SELECT r.*,u.name reviewer_name FROM task_reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.task_id=?", (task_id,)).fetchone(); conn.close()
    if not row: fail(404, "NOT_FOUND", "任务尚未评价")
    return dict(row)


@router.get("/api/tasks/{task_id}/review/history")
def get_task_review_history(task_id: int, request: Request) -> dict[str, Any]:
    conn = db(); task = task_row(conn, task_id); ensure_project_access(conn, task["project_id"], request)
    rows = conn.execute("SELECT h.*,u.name reviewer_name FROM task_review_history h JOIN users u ON u.id=h.reviewer_id WHERE h.task_id=? ORDER BY h.id DESC", (task_id,)).fetchall(); conn.close(); return {"items": [dict(row) for row in rows]}

__all__ = ['_task_log', 'list_tasks', 'create_task', 'get_task', 'update_task', 'assign_task', 'assign_task_in_connection', '_task_action', 'start_task', 'pause_task', 'resume_task', 'complete_task', 'overdue_task', 'unfinished_task', 'task_logs', 'delete_task', 'create_checkin', 'list_task_checkins', 'list_project_checkins', 'review_task', 'get_task_review', 'get_task_review_history']
