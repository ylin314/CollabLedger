from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.core.context import *

from backend.services.recommend import (
    RECOMMEND_DISCLAIMER,
    RECOMMEND_WEIGHTS,
    llm_json,
    batch_recommendations,
    build_recommendation_payload,
    decide_recommendation,
    internal_recommendations,
    list_recommendation_history,
    persist_recommendation_record,
    recommendations,
)

LOAD_LABELS = {"low": "低负载", "normal": "正常", "high": "高负载"}


def internal_member_load(project_id: int) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    members = conn.execute("SELECT u.id user_id,u.name,u.max_concurrent_tasks FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id", (project_id,)).fetchall()
    result = []
    for member in members:
        tasks = conn.execute("SELECT id,COALESCE(estimated_hours,0) estimated_hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL AND status IN ('assigned','in_progress','paused','overdue')", (project_id, member["user_id"])).fetchall()
        current = len(tasks); maximum = max(1, member["max_concurrent_tasks"]); ratio = current / maximum
        level = "low" if ratio < .5 else ("normal" if ratio <= .8 else "high")
        result.append({
            "user_id": member["user_id"], "name": member["name"], "current_task_count": current, "max_concurrent_tasks": maximum,
            "remaining_capacity": max(0, maximum - current), "load_ratio": round(ratio, 2), "load_level": level,
            "load_label": LOAD_LABELS[level], "overloaded": current >= maximum,
            "estimated_hours": round(sum(task["estimated_hours"] for task in tasks), 2),
            "active_task_ids": [task["id"] for task in tasks],
            "rule": "进行中 / 已分配 / 暂停 / 延期任务计入当前负载；达到最大并发任务数视为超负载",
        })
    conn.close()
    return {"project_id": project_id, "generated_at": now_iso(), "rule": "负载 = 当前占用任务数 / 最大并发任务数；<0.5 低负载，0.5-0.8 正常，>0.8 高负载", "members": result}




def _risk_item(kind: str, level: str, message: str, rule: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "level": level, "message": message, "rule": rule, **extra}


def internal_project_risks(project_id: int) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id); today = utc_today(); soon = today + timedelta(days=3); risks: list[dict[str, Any]] = []
    tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status!='completed'", (project_id,)).fetchall()
    for task in tasks:
        if task["status"] in ("overdue", "unfinished") or (task["due_date"] and task["due_date"] < today.isoformat()):
            risks.append(_risk_item("overdue_task", "high", f"任务「{task['title']}」已延期", "状态为延期/未完成，或截止日期早于今天", task_id=task["id"], due_date=task["due_date"], status=task["status"]))
        elif task["due_date"] and task["due_date"] <= soon.isoformat():
            risks.append(_risk_item("upcoming_deadline", "medium", f"任务「{task['title']}」临近截止", "截止日期在未来 3 天内", task_id=task["id"], due_date=task["due_date"], status=task["status"]))
        if task["assignee_id"] is None:
            risks.append(_risk_item("unassigned_task", "medium", f"任务「{task['title']}」尚未分配", "任务没有负责人", task_id=task["id"], due_date=task["due_date"], status=task["status"]))
    for member in internal_member_load(project_id)["members"]:
        if member["load_level"] == "high":
            risks.append(_risk_item("high_member_load", "medium", f"{member['name']}当前负载为 {member['current_task_count']}/{member['max_concurrent_tasks']}", "当前占用任务数 / 最大并发任务数 > 0.8", user_id=member["user_id"], current_task_count=member["current_task_count"], max_concurrent_tasks=member["max_concurrent_tasks"]))
    last = conn.execute("SELECT MAX(at) at FROM task_logs l JOIN tasks t ON t.id=l.task_id WHERE t.project_id=?", (project_id,)).fetchone()["at"]
    if last:
        try:
            if datetime.fromisoformat(last.replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(days=7):
                risks.append(_risk_item("no_recent_activity", "low", "项目最近 7 天没有任务活动", "最近一次任务日志早于 7 天"))
        except ValueError:
            pass
    conn.close()
    return {"project_id": project_id, "generated_at": now_iso(), "count": len(risks), "risks": risks, "rule": "覆盖延期、临近截止、无负责人和高负载四类风险"}


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
    member_rows = conn.execute("SELECT u.id,u.name FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id", (project_id,)).fetchall()
    members = []
    for member in member_rows:
        ms = conn.execute("""SELECT SUM(CASE WHEN status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ? THEN 1 ELSE 0 END) completed_tasks,SUM(CASE WHEN status IN ('assigned','in_progress','paused','overdue') THEN 1 ELSE 0 END) active_tasks,COALESCE(SUM(CASE WHEN substr(updated_at,1,10) BETWEEN ? AND ? THEN actual_hours ELSE 0 END),0) hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL""", (start_s, end_s, start_s, end_s, project_id, member["id"])).fetchone()
        ci = conn.execute("SELECT COUNT(*) n,COALESCE(SUM(hours),0) hours FROM task_checkins WHERE project_id=? AND user_id=? AND substr(created_at,1,10) BETWEEN ? AND ?", (project_id, member["id"], start_s, end_s)).fetchone()
        members.append({"user_id": member["id"], "name": member["name"], "completed_tasks": ms["completed_tasks"] or 0, "active_tasks": ms["active_tasks"] or 0, "checkin_count": ci["n"], "actual_hours": round((ms["hours"] or 0) + (ci["hours"] or 0), 2)})
    conn.close()
    risk_data = internal_project_risks(project_id)
    risks = [item["message"] for item in risk_data["risks"][:5]]
    next_actions: list[str] = []
    if any(item["type"] == "unassigned_task" for item in risk_data["risks"]): next_actions.append("优先分配未分配任务")
    if any(item["type"] == "overdue_task" for item in risk_data["risks"]): next_actions.append("为延期任务调整排期")
    if any(item["type"] == "high_member_load" for item in risk_data["risks"]): next_actions.append("为高负载成员分流任务")
    if not next_actions: next_actions.append("按当前计划继续推进并及时打卡")
    summary = {"tasks_total": total, "tasks_completed": completed, "tasks_in_progress": in_progress, "tasks_overdue": overdue, "checkin_count": checkins["n"], "contribution_count": contributions, "actual_hours": round((task_hours or 0) + (checkins["hours"] or 0), 2)}
    member_summaries, member_source, member_err = _llm_member_summaries(project_id, start, members)
    for member in members:
        if member_summaries.get(member["user_id"]):
            member["summary"] = member_summaries[member["user_id"]]; member["summary_source"] = member_source
        else:
            member["summary"] = f"本周完成 {member['completed_tasks']} 项任务，打卡 {member['checkin_count']} 次，实际工时 {member['actual_hours']} 小时"; member["summary_source"] = "rule"
    insight, insight_source, insight_err = _llm_overall_insight(project_id, start, summary, risks)
    if not insight:
        insight = _rule_insight(summary, risks, next_actions); insight_source = "rule"
    if member_source == "llm" and insight_source == "llm": source = "llm"
    elif "llm" in (member_source, insight_source): source = "mixed"
    else: source = "rule"
    llm_error = "; ".join([err for err in (member_err, insight_err) if err]) or None
    return {
        "project_id": project_id, "project_name": project["name"],
        "period": {"start_date": start_s, "end_date": end_s, "week_start": start_s},
        "summary": summary, "highlights": highlights, "risks": risks, "next_actions": next_actions,
        "members": members, "insight": insight, "insight_source": insight_source,
        "source": source, "llm_error": llm_error, "generated_at": now_iso(), "stored": False,
        "disclaimer": "周报只汇总已有项目事实，不虚构完成情况。",
    }


def _rule_insight(summary: dict[str, Any], risks: list[str], next_actions: list[str]) -> str:
    progress = "正常" if summary["tasks_completed"] > 0 else "起步阶段"
    texts = [f"本周整体进度{progress}，共完成 {summary['tasks_completed']} 项任务，进行中 {summary['tasks_in_progress']} 项，延期 {summary['tasks_overdue']} 项。"]
    if risks:
        urgent = risks[0]
        texts.append(f"当前主要风险：{urgent}。")
    if next_actions:
        texts.append(f"下一步建议：{'；'.join(next_actions)}。")
    return "".join(texts)


def _llm_member_summaries(project_id: int, week: date, member_stats: list[dict[str, Any]]) -> tuple[dict[int, str], str, Optional[str]]:
    """LLM 逐成员摘要；失败回退规则文本。"""
    if not member_stats or not AgentConfigAvailable():
        return {}, "rule", None
    payload = {
        "project_id": project_id, "week_start": week.isoformat(),
        "members": [{"user_id": m["user_id"], "name": m["name"], "completed_tasks": m["completed_tasks"], "active_tasks": m["active_tasks"], "checkin_count": m["checkin_count"], "actual_hours": m["actual_hours"]} for m in member_stats],
    }
    prompt = (
        "你是协作账本的周报助手。为每位成员写一句中文产出摘要，只依据注入的事实统计，禁止编造、禁止排名、禁止人格标签。"
        "返回 JSON 对象，含 summaries 数组，每项含 user_id 与 summary。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        data = llm_json(prompt, _weekly_llm_timeout())
        result = {int(item["user_id"]): str(item.get("summary") or "").strip() for item in data.get("summaries") or [] if item.get("summary")}
        return result, "llm", None if result else "LLM 未返回任何成员摘要"
    except Exception as exc:
        return {}, "rule", str(exc)


def _llm_overall_insight(project_id: int, week: date, summary: dict[str, Any], risks: list[str]) -> tuple[str, str, Optional[str]]:
    """LLM 整体洞察：进度一句 + 风险归因一句 + 下步建议一句，不含排名。失败回退规则 next_actions。"""
    if not AgentConfigAvailable():
        return "", "rule", None
    payload = {"project_id": project_id, "week_start": week.isoformat(), "summary": summary, "risks": risks}
    prompt = (
        "你是协作账本的周报助手。基于注入的本周真实统计与风险，写一句中文整体洞察，包含：整体进度一句、风险归因一句、下步建议一句；"
        "禁止编造数字、禁止排名、禁止人格标签。返回 JSON 对象，含 insight 字符串。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        data = llm_json(prompt, _weekly_llm_timeout())
        text = str(data.get("insight") or "").strip()
        return (text, "llm", None) if text else ("", "rule", "LLM 未返回整体洞察")
    except Exception as exc:
        return "", "rule", str(exc)


def AgentConfigAvailable() -> bool:
    from backend.agent.config import AgentConfig
    return AgentConfig.from_env().configured


def _weekly_llm_timeout() -> float:
    import os
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))


def get_weekly_report(project_id: int, week_start: Optional[date] = None, refresh: bool = False, actor_id: Optional[int] = None) -> dict[str, Any]:
    """获取指定周周报：首次落库、再次读库；refresh=1 强制重新生成并覆盖该周期。"""
    today = utc_today()
    start = week_start or (today - timedelta(days=today.weekday()))
    start = start - timedelta(days=start.weekday())  # 归一化为周一
    end = start + timedelta(days=6)
    conn = db(); ensure_project(conn, project_id)
    start_s, end_s = start.isoformat(), end.isoformat()
    row = conn.execute("SELECT * FROM weekly_reports WHERE project_id=? AND period_start=?", (project_id, start_s)).fetchone()
    if row and not refresh:
        try:
            data = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        data["stored"] = True
        conn.close()
        return data
    data = internal_weekly_report(project_id, start, end)
    data["stored"] = True
    stamp = now_iso()
    if row:
        conn.execute("UPDATE weekly_reports SET payload=?,source=?,llm_error=?,updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), data.get("source") or "rule", data.get("llm_error"), stamp, row["id"]))
    else:
        conn.execute("INSERT INTO weekly_reports(project_id,period_start,period_end,payload,source,llm_error,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)", (project_id, start_s, end_s, json.dumps(data, ensure_ascii=False), data.get("source") or "rule", data.get("llm_error"), actor_id, stamp))
    conn.commit(); conn.close()
    return data


def list_weekly_reports(project_id: int, limit: int = 20, before: Optional[date] = None) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    args: list[Any] = [project_id]
    where = "project_id=?"
    if before is not None:
        where += " AND period_start<?"; args.append(before.isoformat())
    rows = conn.execute(f"SELECT id,project_id,period_start,period_end,source,llm_error,created_by,created_at,updated_at,payload FROM weekly_reports WHERE {where} ORDER BY period_start DESC LIMIT ?", (*args, limit)).fetchall()
    conn.close()
    items = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        summary = payload.get("summary") or {}
        creator = None
        if row["created_by"] is not None:
            uconn = db()
            u = uconn.execute("SELECT name FROM users WHERE id=?", (row["created_by"],)).fetchone()
            creator = u["name"] if u else None
            uconn.close()
        items.append({
            "id": row["id"], "project_id": row["project_id"], "period_start": row["period_start"], "period_end": row["period_end"],
            "source": row["source"], "llm_error": row["llm_error"], "created_by": creator, "created_at": row["created_at"], "updated_at": row["updated_at"],
            "tasks_completed": summary.get("tasks_completed"), "checkin_count": summary.get("checkin_count"), "risks_count": len(payload.get("risks") or []),
        })
    return {"project_id": project_id, "count": len(items), "items": items}


def _weekly_markdown(data: dict[str, Any]) -> str:
    summary = data["summary"]
    lines = [f"# {data['project_name']} 周报", "", f"统计周期：{data['period']['start_date']} 至 {data['period']['end_date']}", "", "## 概览", f"- 任务总数：{summary['tasks_total']}", f"- 本周完成：{summary['tasks_completed']}", f"- 进行中：{summary['tasks_in_progress']}", f"- 延期：{summary['tasks_overdue']}", f"- 打卡次数：{summary['checkin_count']}", f"- 实际工时：{summary['actual_hours']}", "", "## 成员产出"]
    members = data.get("members") or []
    if members:
        lines.append("> 不排名，仅展示每人本周事实与产出摘要")
        for member in members:
            lines.append(f"- {member['name']}：{member.get('summary') or f"完成 {member['completed_tasks']} 项、打卡 {member['checkin_count']} 次、工时 {member['actual_hours']} 小时"}")
    else:
        lines.append("- 本周暂无成员产出数据")
    lines.extend(["", "## 完成亮点"])
    lines.extend([f"- {item}" for item in data["highlights"]] or ["- 暂无已完成任务"])
    lines.extend(["", "## 整体洞察", data.get("insight") or "（未生成洞察）", "", "## 风险", *([f"- {item}" for item in data["risks"]] or ["- 暂无明显风险"]), "", "## 下一步", *[f"- {item}" for item in data["next_actions"]], "", data.get("disclaimer") or "", f"生成时间：{data['generated_at']}", f"来源：{data.get('source') or 'rule'}"])
    return "\n".join(line for line in lines if line is not None)


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
    conn.close()
    return {"project": detail, "members": members, "tasks": tasks, "report": internal_project_report(project_id), "risks": internal_project_risks(project_id), "load": internal_member_load(project_id)}


def list_members_internal(conn, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT m.user_id,u.name,m.role,u.skills,u.max_concurrent_tasks,u.status,m.joined_at FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY m.joined_at", (project_id,)).fetchall(); result = []
    for row in rows:
        item = dict(row); item["skills"] = json.loads(item["skills"] or "[]"); result.append(item)
    return result

__all__ = ['internal_member_load', 'internal_recommendations', 'recommendations', 'persist_recommendation_record', 'build_recommendation_payload', 'batch_recommendations', 'list_recommendation_history', 'decide_recommendation', 'internal_project_risks', 'internal_project_report', '_week_bounds', 'internal_weekly_report', 'get_weekly_report', 'list_weekly_reports', '_weekly_markdown', '_report_markdown', '_simple_pdf_bytes', 'internal_project_snapshot', 'list_members_internal', 'RECOMMEND_WEIGHTS', 'RECOMMEND_DISCLAIMER']
