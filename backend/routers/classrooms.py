from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, Response

from backend.core.context import db, fail, now_iso, require_user
from backend.schemas import ClassroomIn, ClassroomMemberIn, ClassroomRoleUpdate

router = APIRouter()

def _access(conn, classroom_id: int, user_id: int, *, manage: bool = False):
    row = conn.execute("SELECT c.*,cm.role classroom_role FROM classrooms c JOIN classroom_memberships cm ON cm.classroom_id=c.id WHERE c.id=? AND cm.user_id=? AND cm.status='active'", (classroom_id, user_id)).fetchone()
    if not row: fail(403, "FORBIDDEN", "没有该班级的访问权限")
    if manage and row["classroom_role"] not in ("owner", "teacher"): fail(403, "FORBIDDEN", "只有班级负责人或教师可以管理成员")
    return row

@router.get("/api/classrooms")
def list_classrooms(request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request)
    rows = conn.execute("SELECT c.*,cm.role,(SELECT COUNT(*) FROM classroom_memberships x WHERE x.classroom_id=c.id AND x.status='active') member_count,(SELECT COUNT(*) FROM projects p WHERE p.classroom_id=c.id AND p.deleted_at IS NULL) project_count FROM classrooms c JOIN classroom_memberships cm ON cm.classroom_id=c.id WHERE cm.user_id=? AND cm.status='active' ORDER BY c.updated_at DESC,c.id DESC", (user["id"],)).fetchall(); conn.close()
    return {"items": [dict(row) for row in rows]}

@router.post("/api/classrooms", status_code=201)
def create_classroom(payload: ClassroomIn, request: Request) -> dict[str, Any]:
    name = payload.name.strip()
    if not name: fail(422, "VALIDATION_ERROR", "班级名称不能为空")
    conn = db(); user = require_user(conn, request); stamp = now_iso(); cur = conn.execute("INSERT INTO classrooms(name,description,owner_id,created_at,updated_at) VALUES (?,?,?,?,?)", (name, payload.description, user["id"], stamp, stamp)); conn.execute("INSERT INTO classroom_memberships(classroom_id,user_id,role,joined_at,status,updated_at) VALUES (?,?,'owner',?,'active',?)", (cur.lastrowid, user["id"], stamp, stamp)); conn.commit(); row = conn.execute("SELECT * FROM classrooms WHERE id=?", (cur.lastrowid,)).fetchone(); conn.close()
    return {**dict(row), "role": "owner", "member_count": 1, "project_count": 0}

@router.get("/api/classrooms/{classroom_id}/members")
def list_classroom_members(classroom_id: int, request: Request) -> dict[str, Any]:
    conn = db(); user = require_user(conn, request); _access(conn, classroom_id, user["id"])
    rows = conn.execute("SELECT cm.user_id,u.name,u.email,u.skills,u.status,u.avatar_url,cm.role,cm.joined_at,(SELECT pc.external_username FROM platform_connections pc WHERE pc.user_id=cm.user_id AND pc.platform='github' AND pc.status='active' ORDER BY pc.id DESC LIMIT 1) github_username FROM classroom_memberships cm JOIN users u ON u.id=cm.user_id WHERE cm.classroom_id=? AND cm.status='active' ORDER BY CASE cm.role WHEN 'owner' THEN 0 WHEN 'teacher' THEN 1 ELSE 2 END,u.name", (classroom_id,)).fetchall(); conn.close()
    return {"items": [dict(row) for row in rows]}

@router.post("/api/classrooms/{classroom_id}/members", status_code=201)
def add_classroom_member(classroom_id: int, payload: ClassroomMemberIn, request: Request) -> dict[str, Any]:
    conn = db(); actor = require_user(conn, request); _access(conn, classroom_id, actor["id"], manage=True); target = conn.execute("SELECT * FROM users WHERE id=?", (payload.user_id,)).fetchone() if payload.user_id is not None else conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (payload.email.strip(),)).fetchone() if payload.email else None
    if not target: conn.close(); fail(404, "NOT_FOUND", "未找到已注册的成员")
    stamp = now_iso(); conn.execute("INSERT INTO classroom_memberships(classroom_id,user_id,role,joined_at,status,updated_at) VALUES (?,?,?,?, 'active',?) ON CONFLICT(classroom_id,user_id) DO UPDATE SET role=excluded.role,status='active',left_at=NULL,updated_at=excluded.updated_at", (classroom_id, target["id"], payload.role, stamp, stamp)); conn.execute("UPDATE classrooms SET updated_at=? WHERE id=?", (stamp, classroom_id)); conn.commit(); conn.close()
    return {"user_id": target["id"], "name": target["name"], "email": target["email"], "role": payload.role, "joined_at": stamp}

@router.patch("/api/classrooms/{classroom_id}/members/{user_id}")
def update_classroom_member(classroom_id: int, user_id: int, payload: ClassroomRoleUpdate, request: Request) -> dict[str, Any]:
    conn = db(); actor = require_user(conn, request); classroom = _access(conn, classroom_id, actor["id"], manage=True); current = conn.execute("SELECT role FROM classroom_memberships WHERE classroom_id=? AND user_id=? AND status='active'", (classroom_id, user_id)).fetchone()
    if not current: conn.close(); fail(404, "NOT_FOUND", "班级成员不存在")
    if current["role"] == "owner" and user_id == classroom["owner_id"] and payload.role != "owner": conn.close(); fail(409, "CONFLICT", "班级必须保留创建者")
    stamp = now_iso(); conn.execute("UPDATE classroom_memberships SET role=?,updated_at=? WHERE classroom_id=? AND user_id=?", (payload.role, stamp, classroom_id, user_id)); conn.commit(); conn.close(); return {"user_id": user_id, "role": payload.role, "updated_at": stamp}

@router.delete("/api/classrooms/{classroom_id}/members/{user_id}", status_code=204)
def remove_classroom_member(classroom_id: int, user_id: int, request: Request) -> Response:
    conn = db(); actor = require_user(conn, request); classroom = _access(conn, classroom_id, actor["id"], manage=True)
    if user_id == classroom["owner_id"]: conn.close(); fail(409, "CONFLICT", "不能移除班级创建者")
    stamp = now_iso(); conn.execute("UPDATE classroom_memberships SET status='left',left_at=?,updated_at=? WHERE classroom_id=? AND user_id=? AND status='active'", (stamp, stamp, classroom_id, user_id)); conn.commit(); conn.close(); return Response(status_code=204)

__all__ = ["router"]

@router.get("/api/users/profile/{user_id}/history")
def collaboration_history(
    user_id: int,
    request: Request,
    project_type: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1900, le=2200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    conn = db()
    current = require_user(conn, request)
    uid = current["id"]
    if user_id == 0:
        user_id = uid
    if user_id != uid:
        shared = conn.execute(
            "SELECT 1 FROM classroom_memberships mine JOIN classroom_memberships theirs ON theirs.classroom_id=mine.classroom_id WHERE mine.user_id=? AND mine.status='active' AND theirs.user_id=? AND theirs.status='active' LIMIT 1",
            (uid, user_id),
        ).fetchone()
        if not shared:
            conn.close()
            fail(403, "FORBIDDEN", "只能查看同班成员的协作履历")
    target = conn.execute("SELECT id,name,email FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        conn.close()
        fail(404, "NOT_FOUND", "用户不存在")

    from backend.services.profile_authorization import profile_source_project_ids

    source_project_ids = profile_source_project_ids(conn, user_id, self_view=user_id == uid)
    marks = ",".join("?" for _ in source_project_ids) or "NULL"
    args: list[Any] = [user_id, *source_project_ids]
    filters = [f"p.id IN ({marks})", "p.deleted_at IS NULL"]
    if project_type:
        filters.append("p.project_type=?")
        args.append(project_type.strip())
    if year is not None:
        year_start = f"{year:04d}-01-01"
        year_end = f"{year:04d}-12-31"
        filters.append("(COALESCE(p.start_date,substr(p.created_at,1,10))<=? AND (p.end_date IS NULL OR p.end_date>=?))")
        args.extend([year_end, year_start])
    where_sql = " AND ".join(filters)
    count = conn.execute(
        f"SELECT COUNT(*) n FROM memberships m JOIN projects p ON p.id=m.project_id WHERE m.user_id=? AND {where_sql}",
        args,
    ).fetchone()["n"]
    offset = (page - 1) * page_size
    project_rows = conn.execute(
        f"""SELECT p.id project_id,p.name,p.project_type,p.status,m.role,p.start_date,p.end_date,
                   SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) tasks_completed,
                   AVG(CASE WHEN t.status='completed' THEN COALESCE(r.quality,t.quality) END) average_quality
            FROM memberships m JOIN projects p ON p.id=m.project_id
            LEFT JOIN tasks t ON t.project_id=p.id AND t.deleted_at IS NULL
              AND (t.assignee_id=? OR EXISTS (
                    SELECT 1 FROM task_participants tp
                    WHERE tp.task_id=t.id AND tp.user_id=? AND tp.status IN ('active','left')
              ))
            LEFT JOIN task_reviews r ON r.task_id=t.id
            WHERE m.user_id=? AND {where_sql}
            GROUP BY p.id,p.name,p.project_type,p.status,m.role,p.start_date,p.end_date
            ORDER BY COALESCE(p.end_date,p.updated_at,p.created_at) DESC,p.id DESC
            LIMIT ? OFFSET ?""",
        [user_id, user_id, *args, page_size, offset],
    ).fetchall()
    page_project_ids = [int(row["project_id"]) for row in project_rows]
    page_marks = ",".join("?" for _ in page_project_ids) or "NULL"
    tasks = conn.execute(
        f"""SELECT t.id,t.project_id,t.title,t.status,t.assignee_id,t.created_at,t.updated_at,p.name project_name
            FROM tasks t JOIN projects p ON p.id=t.project_id
            WHERE t.deleted_at IS NULL AND p.deleted_at IS NULL AND p.id IN ({page_marks})
              AND (t.assignee_id=? OR EXISTS (
                    SELECT 1 FROM task_participants tp
                    WHERE tp.task_id=t.id AND tp.user_id=? AND tp.status IN ('active','left')
              ))
            ORDER BY t.updated_at DESC,t.id DESC LIMIT 100""",
        [*page_project_ids, user_id, user_id],
    ).fetchall()
    contributions = conn.execute(
        f"""SELECT c.id,c.project_id,c.title,c.kind,c.quantity,c.status,c.occurred_at,c.created_at,p.name project_name
            FROM contributions c JOIN projects p ON p.id=c.project_id
            WHERE c.user_id=? AND c.deleted_at IS NULL AND p.deleted_at IS NULL AND p.id IN ({page_marks})
            ORDER BY c.created_at DESC LIMIT 100""",
        [user_id, *page_project_ids],
    ).fetchall()
    conn.close()
    items = []
    for row in project_rows:
        item = dict(row)
        if item.get("average_quality") is not None:
            item["average_quality"] = round(float(item["average_quality"]), 2)
        item["tasks_completed"] = int(item.get("tasks_completed") or 0)
        items.append(item)
    return {
        "user": dict(target),
        "items": items,
        "total": int(count or 0),
        "page": page,
        "page_size": page_size,
        "projects": items,
        "tasks": [dict(row) for row in tasks],
        "contributions": [dict(row) for row in contributions],
    }


@router.get("/api/users/me/history")
def my_collaboration_history(
    request: Request,
    project_type: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1900, le=2200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return collaboration_history(
        0,
        request,
        project_type=project_type,
        year=year,
        page=page,
        page_size=page_size,
    )

