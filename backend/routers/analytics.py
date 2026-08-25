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
from backend.services.analytics import *

@router.get("/api/projects/{project_id}/members/load")
def members_load(project_id: int, request: Request) -> dict[str, Any]:
    conn = db(); ensure_project_access(conn, project_id, request); conn.close(); return internal_member_load(project_id)


@router.get("/api/projects/{project_id}/recommendations")
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


@router.get("/api/projects/{project_id}/risks")
def project_risks(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request, allow_internal=request is None); conn.close(); return internal_project_risks(project_id)


@router.get("/api/projects/{project_id}/report")
def project_report(project_id: int, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db(); ensure_project_access(conn, project_id, request, allow_internal=request is None); conn.close(); return internal_project_report(project_id)


@router.get("/api/projects/{project_id}/contribution-report")
def contribution_report(project_id: int, request: Request) -> dict[str, Any]:
    return project_report(project_id, request)


@router.get("/api/projects/{project_id}/weekly-report")
def weekly_report(project_id: int, request: Request, start_date: Optional[date] = None, end_date: Optional[date] = None, format: Literal["json", "markdown"] = "json") -> Any:
    conn = db(); ensure_project_access(conn, project_id, request); conn.close(); start, end = _week_bounds(start_date, end_date); data = internal_weekly_report(project_id, start, end)
    if format == "markdown": return PlainTextResponse(_weekly_markdown(data), media_type="text/markdown; charset=utf-8")
    return data


@router.get("/api/projects/{project_id}/report/export")
def export_report(project_id: int, request: Request, format: Literal["markdown", "pdf"] = "markdown") -> Response:
    conn = db(); project, _, _ = ensure_project_access(conn, project_id, request); conn.close(); data = internal_project_report(project_id)
    if format == "pdf":
        return Response(_simple_pdf_bytes(f"CollabLedger project report #{project_id}"), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="project-{project_id}-report.pdf"'})
    return PlainTextResponse(_report_markdown(data), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="project-{project_id}-report.md"'})

__all__ = ['members_load', 'get_recommendations', 'project_risks', 'project_report', 'contribution_report', 'weekly_report', 'export_report']
