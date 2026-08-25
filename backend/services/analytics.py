from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.core.context import *

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


def internal_project_report(project_id: int) -> dict[str, Any]:
    conn = db(); project = ensure_project(conn, project_id); stats = _project_stats(conn, project_id); members = conn.execute("SELECT u.id,u.name FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id", (project_id,)).fetchall(); items = []
    for member in members:
        task_stats = conn.execute("""SELECT COUNT(*) total,SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed,SUM(CASE WHEN status IN ('overdue','unfinished') THEN 1 ELSE 0 END) overdue,SUM(COALESCE(actual_hours,0)) hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL""", (project_id, member["id"])).fetchone()
        quality = conn.execute("SELECT AVG(COALESCE(r.quality,t.quality)) q FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id WHERE t.project_id=? AND t.assignee_id=? AND (r.quality IS NOT NULL OR t.quality IS NOT NULL)", (project_id, member["id"])).fetchone()["q"]
        contribs = conn.execute("SELECT kind,SUM(quantity) quantity FROM contributions WHERE project_id=? AND user_id=? AND status='confirmed' AND deleted_at IS NULL GROUP BY kind ORDER BY kind", (project_id, member["id"])).fetchall()
        items.append({"user_id": member["id"], "name": member["name"], "tasks_total": task_stats["total"] or 0, "tasks_completed": task_stats["completed"] or 0, "tasks_overdue": task_stats["overdue"] or 0, "average_quality": round(quality, 2) if quality is not None else None, "actual_hours": round(task_stats["hours"] or 0, 2), "contributions": [dict(row) for row in contribs]})
    conn.close(); return {"project_id": project_id, "project_name": project["name"], "generated_at": now_iso(), "overall": {"tasks_total": stats["task_count"], "tasks_completed": stats["completed_task_count"], "tasks_in_progress": stats["in_progress_task_count"], "tasks_overdue": stats["overdue_task_count"], "progress": stats["progress"]}, "members": items}


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

__all__ = ['internal_member_load', 'internal_recommendations', 'recommendations', 'internal_project_risks', 'internal_project_report', '_week_bounds', 'internal_weekly_report', '_weekly_markdown', '_report_markdown', '_simple_pdf_bytes', 'internal_project_snapshot', 'list_members_internal']
