from __future__ import annotations

import json
import os
import re
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


ACTIVE_LOAD_STATUSES = ("assigned", "in_progress", "paused", "overdue")
LOAD_WEIGHT_DEFAULTS = {"in_progress": 1.0, "assigned": 0.6, "paused": 0.5, "overdue": 1.3}


def _load_weight(status: str) -> float:
    """任务状态权重，可经环境变量覆盖：LOAD_WEIGHT_IN_PROGRESS / LOAD_WEIGHT_ASSIGNED / LOAD_WEIGHT_PAUSED / LOAD_WEIGHT_OVERDUE。"""
    env_key = {"in_progress": "LOAD_WEIGHT_IN_PROGRESS", "assigned": "LOAD_WEIGHT_ASSIGNED", "paused": "LOAD_WEIGHT_PAUSED", "overdue": "LOAD_WEIGHT_OVERDUE"}.get(status)
    if env_key is None:
        return LOAD_WEIGHT_DEFAULTS.get(status, 1.0)
    try:
        return float(os.getenv(env_key, str(LOAD_WEIGHT_DEFAULTS[status])))
    except (TypeError, ValueError):
        return LOAD_WEIGHT_DEFAULTS[status]


def internal_member_load(project_id: int) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    members = conn.execute("SELECT u.id user_id,u.name,u.max_concurrent_tasks FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? AND m.status='active' ORDER BY u.id", (project_id,)).fetchall()
    result = []
    for member in members:
        tasks = conn.execute("SELECT id,status,COALESCE(estimated_hours,0) estimated_hours FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL AND status IN ('assigned','in_progress','paused','overdue')", (project_id, member["user_id"])).fetchall()
        current = len(tasks); maximum = max(1, member["max_concurrent_tasks"]); ratio = current / maximum
        level = "low" if ratio < .5 else ("normal" if ratio <= .8 else "high")
        weighted = sum(_load_weight(task["status"]) for task in tasks) / maximum
        weighted_level = "low" if weighted < .5 else ("normal" if weighted <= .8 else "high")
        result.append({
            "user_id": member["user_id"], "name": member["name"], "current_task_count": current, "max_concurrent_tasks": maximum,
            "remaining_capacity": max(0, maximum - current), "load_ratio": round(ratio, 2), "load_level": level,
            "load_label": LOAD_LABELS[level], "overloaded": current >= maximum,
            "estimated_hours": round(sum(task["estimated_hours"] for task in tasks), 2),
            "active_task_ids": [task["id"] for task in tasks],
            "weighted_load": round(weighted, 2),
            "weighted_level": weighted_level,
            "weighted_label": LOAD_LABELS[weighted_level],
            "weighted_overdue_tasks": sum(1 for task in tasks if task["status"] == "overdue"),
            "rule": "进行中 / 已分配 / 暂停 / 延期任务计入当前负载；未完成（unfinished）是项目结束后的终态，不计当前负载",
        })
    conn.close()
    return {"project_id": project_id, "generated_at": now_iso(), "active_statuses": list(ACTIVE_LOAD_STATUSES), "rule": "负载 = 当前占用任务数 / 最大并发任务数；加权负载 = Σ状态权重 / 最大并发任务数（进行中 1.0、已分配 0.6、暂停 0.5、延期 1.3）；未完成（unfinished）不计当前负载；<0.5 低负载，0.5-0.8 正常，>0.8 高负载", "members": result}




RISK_SEVERITY = {
    "critical_unassigned": 95,
    "overdue_task": 90,
    "upcoming_deadline": 70,
    "high_member_load": 65,
    "unassigned_task": 60,
    "no_recent_activity": 30,
}


def _risk_item(kind: str, level: str, message: str, rule: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "level": level, "message": message, "rule": rule, "severity": RISK_SEVERITY.get(kind, 50), **extra}


def _rule_risk_summary(risks: list[dict[str, Any]]) -> str:
    if not risks:
        return "当前未发现明显项目风险。"
    first = risks[0]
    return f"当前共有 {len(risks)} 个项目风险，优先关注：{first.get('message')}。建议按风险严重度逐项处理。"


def _safe_llm_error(exc: Exception) -> str:
    """返回可诊断但不泄露凭据的简短错误。"""
    text = str(exc).strip() or type(exc).__name__
    for env_key in ("LLM_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(env_key, "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)((?:authorization|api[_-]?key|token|bearer)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:api_key|key|token)=)[^&\s]+", r"\1[REDACTED]", text)
    return text[:240]


def _llm_risk_summary(project_id: int, risks: list[dict[str, Any]]) -> tuple[str, str, str, Optional[str]]:
    """LLM 生成风险总结；失败时显式返回规则回退状态和脱敏错误。"""
    fallback = _rule_risk_summary(risks)
    if not AgentConfigAvailable():
        return fallback, "rule", "not_configured", None
    facts = [{"type": r.get("type"), "level": r.get("level"), "message": r.get("message"), "rule": r.get("rule")} for r in risks[:8]]
    prompt = (
        "你是协作账本的风险总结助手。只依据给定的风险事实，用 1-2 句中文总结当前最需要关注的风险并给出下一步建议。"
        "禁止编造事实，禁止点名批评成员，不使用负面人格标签。只返回 JSON：{\"summary\": \"...\"}。"
        f"项目事实：{json.dumps({'project_id': project_id, 'risks': facts}, ensure_ascii=False)}"
    )
    try:
        data = llm_json(prompt, timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "45")))
        text = str(data.get("summary") or "").strip()
        if text:
            return text, "llm", "ok", None
        return fallback, "rule", "failed", "LLM 未返回风险总结"
    except Exception as exc:
        return fallback, "rule", "failed", _safe_llm_error(exc)


def internal_project_risks(project_id: int, summarize: bool = False) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id); today = utc_today(); soon = today + timedelta(days=3); risks: list[dict[str, Any]] = []
    tasks = conn.execute("SELECT * FROM tasks WHERE project_id=? AND deleted_at IS NULL AND status!='completed'", (project_id,)).fetchall()
    for task in tasks:
        if task["status"] in ("overdue", "unfinished") or (task["due_date"] and task["due_date"] < today.isoformat()):
            risks.append(_risk_item("overdue_task", "high", f"任务「{task['title']}」已延期", "状态为延期/未完成，或截止日期早于今天", task_id=task["id"], due_date=task["due_date"], status=task["status"]))
        elif task["due_date"] and task["due_date"] <= soon.isoformat():
            risks.append(_risk_item("upcoming_deadline", "medium", f"任务「{task['title']}」临近截止", "截止日期在未来 3 天内", task_id=task["id"], due_date=task["due_date"], status=task["status"]))
        if task["assignee_id"] is None:
            if task["priority"] == "high":
                risks.append(_risk_item("critical_unassigned", "high", f"关键任务「{task['title']}」无人承接", "高优先级任务未分配负责人（已聚合普通未分配风险）", task_id=task["id"], due_date=task["due_date"], status=task["status"], source_types=["critical_unassigned", "unassigned_task"], unassigned_category="critical"))
            else:
                risks.append(_risk_item("unassigned_task", "medium", f"任务「{task['title']}」尚未分配", "任务没有负责人", task_id=task["id"], due_date=task["due_date"], status=task["status"], source_types=["unassigned_task"], unassigned_category="normal"))
    for member in internal_member_load(project_id)["members"]:
        if member["weighted_level"] == "high":
            risks.append(_risk_item("high_member_load", "medium", f"{member['name']}当前加权负载为 {member['weighted_load']:.2f}", "加权负载 = Σ状态权重 / 最大并发任务数，> 0.8 为高负载", user_id=member["user_id"], current_task_count=member["current_task_count"], max_concurrent_tasks=member["max_concurrent_tasks"], load_ratio=member["load_ratio"], load_level=member["load_level"], weighted_load=member["weighted_load"], weighted_level=member["weighted_level"]))
    last = conn.execute("SELECT MAX(at) at FROM task_logs l JOIN tasks t ON t.id=l.task_id WHERE t.project_id=?", (project_id,)).fetchone()["at"]
    if last:
        try:
            if datetime.fromisoformat(last.replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(days=7):
                risks.append(_risk_item("no_recent_activity", "low", "项目最近 7 天没有任务活动", "最近一次任务日志早于 7 天"))
        except ValueError:
            pass
    risks.sort(key=lambda item: item.get("severity", 0), reverse=True)
    payload: dict[str, Any] = {"project_id": project_id, "generated_at": now_iso(), "count": len(risks), "risks": risks, "rule": "覆盖延期、临近截止、无负责人和高负载四类风险；按严重度降序排列"}
    if summarize and risks:
        summary, summary_source, llm_status, llm_error = _llm_risk_summary(project_id, risks)
        payload["summary"] = summary
        payload["summary_source"] = summary_source
        payload["llm_status"] = llm_status
        payload["llm_error"] = llm_error
    conn.close()
    return payload


def internal_project_report(project_id: int) -> dict[str, Any]:
    conn = db(); project = ensure_project(conn, project_id); stats = _project_stats(conn, project_id); members = conn.execute("SELECT u.id,u.name FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? AND m.status='active' ORDER BY u.id", (project_id,)).fetchall(); items = []
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
    """从真实项目表生成周报；当前成员统计排除退出成员，待处置风险仍保留，历史原始数据不删除。"""
    conn = db(); project = ensure_project(conn, project_id)
    start_s, end_s = start.isoformat(), end.isoformat()
    date_expr = "COALESCE(substr(occurred_at,1,10),substr(created_at,1,10))"
    active_task_scope = "(assignee_id IS NULL OR EXISTS (SELECT 1 FROM memberships active_members WHERE active_members.project_id=tasks.project_id AND active_members.user_id=tasks.assignee_id AND active_members.status='active'))"
    active_checkin_scope = "EXISTS (SELECT 1 FROM memberships active_members WHERE active_members.project_id=task_checkins.project_id AND active_members.user_id=task_checkins.user_id AND active_members.status='active')"
    active_contribution_scope = "EXISTS (SELECT 1 FROM memberships active_members WHERE active_members.project_id=contributions.project_id AND active_members.user_id=contributions.user_id AND active_members.status='active')"
    task_total = conn.execute(
        f"SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND {active_task_scope}",
        (project_id,),
    ).fetchone()["n"]
    completed = conn.execute(
        f"SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND {active_task_scope} "
        "AND status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ?",
        (project_id, start_s, end_s),
    ).fetchone()["n"]
    in_progress = conn.execute(
        f"SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND {active_task_scope} AND status='in_progress'",
        (project_id,),
    ).fetchone()["n"]
    overdue = conn.execute(
        f"SELECT COUNT(*) n FROM tasks WHERE project_id=? AND deleted_at IS NULL AND {active_task_scope} "
        "AND status IN ('overdue','unfinished')",
        (project_id,),
    ).fetchone()["n"]
    checkins = conn.execute(
        "SELECT COUNT(*) n,COALESCE(SUM(hours),0) hours FROM task_checkins "
        f"WHERE project_id=? AND substr(created_at,1,10) BETWEEN ? AND ? AND {active_checkin_scope}",
        (project_id, start_s, end_s),
    ).fetchone()
    contribution_stats = conn.execute(
        f"SELECT "
        f"SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) confirmed_count, "
        f"SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending_count, "
        f"SUM(CASE WHEN status='disputed' THEN 1 ELSE 0 END) disputed_count "
        f"FROM contributions WHERE project_id=? AND deleted_at IS NULL AND {active_contribution_scope} AND {date_expr} BETWEEN ? AND ?",
        (project_id, start_s, end_s),
    ).fetchone()
    task_hours_all = conn.execute(
        f"SELECT COALESCE(SUM(actual_hours),0) hours FROM tasks WHERE project_id=? "
        f"AND deleted_at IS NULL AND {active_task_scope} AND substr(updated_at,1,10) BETWEEN ? AND ?",
        (project_id, start_s, end_s),
    ).fetchone()["hours"] or 0
    highlights = [
        row["title"] for row in conn.execute(
            f"SELECT title FROM tasks WHERE project_id=? AND deleted_at IS NULL AND {active_task_scope} "
            "AND status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ? "
            "ORDER BY updated_at DESC LIMIT 5",
            (project_id, start_s, end_s),
        ).fetchall()
    ]
    member_rows = conn.execute(
        "SELECT u.id,u.name FROM users u JOIN memberships m ON m.user_id=u.id "
        "WHERE m.project_id=? AND m.status='active' ORDER BY u.id",
        (project_id,),
    ).fetchall()
    members = []
    for member in member_rows:
        ms = conn.execute(
            "SELECT "
            "SUM(CASE WHEN status='completed' AND substr(updated_at,1,10) BETWEEN ? AND ? THEN 1 ELSE 0 END) completed_tasks,"
            "SUM(CASE WHEN status IN ('assigned','in_progress','paused','overdue') THEN 1 ELSE 0 END) active_tasks,"
            "COALESCE(SUM(CASE WHEN substr(updated_at,1,10) BETWEEN ? AND ? THEN actual_hours ELSE 0 END),0) task_hours "
            "FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL",
            (start_s, end_s, start_s, end_s, project_id, member["id"]),
        ).fetchone()
        ci = conn.execute(
            "SELECT COUNT(*) n,COALESCE(SUM(hours),0) hours FROM task_checkins "
            f"WHERE project_id=? AND user_id=? AND substr(created_at,1,10) BETWEEN ? AND ? AND {active_checkin_scope}",
            (project_id, member["id"], start_s, end_s),
        ).fetchone()
        cs = conn.execute(
            f"SELECT "
            f"SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) confirmed_count, "
            f"SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending_count, "
            f"SUM(CASE WHEN status='disputed' THEN 1 ELSE 0 END) disputed_count "
            f"FROM contributions WHERE project_id=? AND user_id=? AND deleted_at IS NULL AND {active_contribution_scope} AND {date_expr} BETWEEN ? AND ?",
            (project_id, member["id"], start_s, end_s),
        ).fetchone()
        checkin_count = ci["n"] or 0
        checkin_hours = round(ci["hours"] or 0, 2)
        task_hours = round(ms["task_hours"] or 0, 2)
        hours_source = "checkin" if checkin_count else "task"
        effective_hours = checkin_hours if checkin_count else task_hours
        pending_count = (cs["pending_count"] or 0) + (cs["disputed_count"] or 0)
        members.append({
            "user_id": member["id"], "name": member["name"],
            "completed_tasks": ms["completed_tasks"] or 0, "active_tasks": ms["active_tasks"] or 0,
            "checkin_count": checkin_count, "checkin_hours": checkin_hours,
            "task_hours": task_hours, "actual_hours": effective_hours,
            "hours_source": hours_source,
            "contribution_count": cs["confirmed_count"] or 0,
            "pending_contribution_count": pending_count,
            "pending_count": cs["pending_count"] or 0,
            "disputed_count": cs["disputed_count"] or 0,
        })
    conn.close()
    risk_data = internal_project_risks(project_id)
    risks = [item["message"] for item in risk_data["risks"][:5]]
    next_actions: list[str] = []
    if any(item["type"] in ("unassigned_task", "critical_unassigned") for item in risk_data["risks"]):
        next_actions.append("优先分配未分配任务")
    if any(item["type"] == "overdue_task" for item in risk_data["risks"]):
        next_actions.append("为延期任务调整排期")
    if any(item["type"] == "high_member_load" for item in risk_data["risks"]):
        next_actions.append("为高负载成员分流任务")
    if not next_actions:
        next_actions.append("按当前计划继续推进并及时打卡")
    confirmed_count = contribution_stats["confirmed_count"] or 0
    pending_count = (contribution_stats["pending_count"] or 0) + (contribution_stats["disputed_count"] or 0)
    total_effective_hours = round(sum(member["actual_hours"] for member in members), 2)
    summary = {
        "tasks_total": task_total, "tasks_completed": completed, "tasks_in_progress": in_progress,
        "tasks_overdue": overdue, "checkin_count": checkins["n"] or 0,
        "checkin_hours": round(checkins["hours"] or 0, 2),
        "task_hours": round(task_hours_all, 2), "actual_hours": total_effective_hours,
        "hours_source": "member-wise_checkin_priority",
        "contribution_count": confirmed_count,
        "pending_contribution_count": pending_count,
        "pending_count": contribution_stats["pending_count"] or 0,
        "disputed_count": contribution_stats["disputed_count"] or 0,
        "pending_label": f"待确认 {pending_count} 项" if pending_count else None,
    }
    member_summaries, member_source, member_err = _llm_member_summaries(project_id, start, members)
    for member in members:
        if member_summaries.get(member["user_id"]):
            member["summary"] = member_summaries[member["user_id"]]; member["summary_source"] = member_source
        else:
            member["summary"] = (
                f"本周完成 {member['completed_tasks']} 项任务，确认贡献 {member['contribution_count']} 项，"
                f"{member['checkin_hours'] if member['hours_source'] == 'checkin' else member['task_hours']} 小时"
            )
            member["summary_source"] = "rule"
    insight_struct, insight_source, insight_err = _llm_overall_insight(project_id, start, summary, risks)
    if insight_struct:
        insight = "".join(
            ["本周亮点：" + "；".join(insight_struct["highlights"]) + "。", "风险与归因：" + "；".join(insight_struct["risks"]) + "。", "下步建议：" + "；".join(insight_struct["actions"]) + "。"]
        )
    else:
        insight = _rule_insight(summary, risks, next_actions); insight_source = "rule"
    if member_source == "llm" and insight_source == "llm": source = "llm"
    elif "llm" in (member_source, insight_source): source = "mixed"
    else: source = "rule"
    llm_error = "; ".join([err for err in (member_err, insight_err) if err]) or None
    return {
        "project_id": project_id, "project_name": project["name"],
        "period": {"start_date": start_s, "end_date": end_s, "week_start": start_s},
        "summary": summary, "highlights": highlights, "risks": risks, "next_actions": next_actions,
        "members": members, "insight_struct": insight_struct or None, "insight": insight, "insight_source": insight_source,
        "source": source, "llm_error": llm_error, "generated_at": now_iso(), "stored": False,
        "disclaimer": "周报只汇总已有项目事实，不虚构完成情况；确认贡献与待确认贡献分开，打卡工时优先且不与任务工时相加。",
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
        "你是协作账本的周报助手。为每位成员写一两句中文产出摘要（产出内容 + 依据的统计事实），只依据注入的事实统计，禁止编造、禁止排名、禁止人格标签。"
        "返回 JSON 对象，含 summaries 数组，每项含 user_id 与 summary。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        data = llm_json(prompt, _weekly_llm_timeout())
        result = {int(item["user_id"]): str(item.get("summary") or "").strip() for item in data.get("summaries") or [] if item.get("summary")}
        return result, "llm", None if result else "LLM 未返回任何成员摘要"
    except Exception as exc:
        return {}, "rule", str(exc)


def _llm_overall_insight(project_id: int, week: date, summary: dict[str, Any], risks: list[str]) -> tuple[dict[str, Any], str, Optional[str]]:
    """LLM 结构化整体分析：亮点/风险归因/下步建议分条输出。失败回退规则。"""
    if not AgentConfigAvailable():
        return {}, "rule", None
    payload = {"project_id": project_id, "week_start": week.isoformat(), "summary": summary, "risks": risks}
    prompt = (
        "你是协作账本的周报分析助手。基于注入的本周真实统计与风险，输出结构化中文分析："
        "highlights 数组（2-3 条本周亮点，须引用注入的真实数字）；risks 数组（1-3 条风险与归因，无风险则给需关注事项）；"
        "actions 数组（2-3 条下步建议，具体可执行）。禁止编造数字、禁止排名、禁止人格标签。"
        "返回 JSON 对象，含 highlights、risks、actions 三个字符串数组。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        data = llm_json(prompt, _weekly_llm_timeout())

        def _clean_list(value):
            result = [str(item).strip() for item in (value or []) if str(item).strip()]
            return result[:4]

        struct = {
            "highlights": _clean_list(data.get("highlights")),
            "risks": _clean_list(data.get("risks")),
            "actions": _clean_list(data.get("actions")),
        }
        if not any(struct.values()):
            return {}, "rule", "LLM 未返回结构化分析"
        return struct, "llm", None
    except Exception as exc:
        return {}, "rule", str(exc)


def AgentConfigAvailable() -> bool:
    from backend.agent.config import AgentConfig
    return AgentConfig.from_env().configured


def _weekly_llm_timeout() -> float:
    import os
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))


def _weekly_period_payload(project_id: int, start: date, end: date) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat(), "week_start": start.isoformat()},
        "exists": False,
        "stored": False,
        "source": "none",
        "summary": None,
        "members": [],
        "highlights": [],
        "risks": [],
        "next_actions": [],
        "disclaimer": "尚未生成本周期周报；请在周报页面点击“生成/刷新周报”。",
    }


def _weekly_row_data(row: Any, project_id: int, start: date, end: date) -> dict[str, Any]:
    try:
        data = json.loads(row["payload"] or "{}")
    except (TypeError, json.JSONDecodeError):
        data = _weekly_period_payload(project_id, start, end)
    data["exists"] = True
    data["stored"] = True
    return data


def get_weekly_report(project_id: int, week_start: Optional[date] = None, **_: Any) -> dict[str, Any]:
    """只读查询已存在的周报；不会因为打开工作区或 Agent 查询而写库。"""
    today = utc_today()
    start = week_start or (today - timedelta(days=today.weekday()))
    start = start - timedelta(days=start.weekday())
    end = start + timedelta(days=6)
    conn = db(); ensure_project(conn, project_id)
    row = conn.execute(
        "SELECT * FROM weekly_reports WHERE project_id=? AND period_start=? AND period_end=?",
        (project_id, start.isoformat(), end.isoformat()),
    ).fetchone()
    if not row:
        conn.close()
        return _weekly_period_payload(project_id, start, end)
    data = _weekly_row_data(row, project_id, start, end)
    conn.close()
    return data


def generate_weekly_report(project_id: int, week_start: Optional[date] = None, actor_id: Optional[int] = None) -> dict[str, Any]:
    """显式生成或刷新周报，并以周期唯一键幂等落库。"""
    today = utc_today()
    start = week_start or (today - timedelta(days=today.weekday()))
    start = start - timedelta(days=start.weekday())
    end = start + timedelta(days=6)
    data = internal_weekly_report(project_id, start, end)
    data["stored"] = True; data["exists"] = True
    stamp = now_iso()
    conn = db(); ensure_project(conn, project_id)
    conn.execute(
        "INSERT INTO weekly_reports(project_id,period_start,period_end,payload,source,llm_error,created_by,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,period_start,period_end) DO UPDATE SET "
        "payload=excluded.payload,source=excluded.source,llm_error=excluded.llm_error,updated_at=excluded.updated_at",
        (project_id, start.isoformat(), end.isoformat(), json.dumps(data, ensure_ascii=False), data.get("source") or "rule", data.get("llm_error"), actor_id, stamp, stamp),
    )
    conn.commit(); conn.close()
    return data

def list_weekly_reports(project_id: int, limit: int = 20, before: Optional[date] = None) -> dict[str, Any]:
    conn = db(); ensure_project(conn, project_id)
    args: list[Any] = [project_id]
    where = "project_id=?"
    if before is not None:
        where += " AND period_start<?"; args.append(before.isoformat())
    rows = conn.execute(f"SELECT w.id,w.project_id,w.period_start,w.period_end,w.source,w.llm_error,w.created_by,u.name created_by_name,w.created_at,w.updated_at,w.payload FROM weekly_reports w LEFT JOIN users u ON u.id=w.created_by WHERE {where.replace('project_id=', 'w.project_id=')} ORDER BY w.period_start DESC LIMIT ?", (*args, limit)).fetchall()
    conn.close()
    items = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        summary = payload.get("summary") or {}
        items.append({
            "id": row["id"], "project_id": row["project_id"], "period_start": row["period_start"], "period_end": row["period_end"],
            "source": row["source"], "llm_error": row["llm_error"], "created_by": row["created_by_name"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "tasks_completed": summary.get("tasks_completed"), "checkin_count": summary.get("checkin_count"), "risks_count": len(payload.get("risks") or []),
        })
    return {"project_id": project_id, "count": len(items), "items": items}


def _weekly_markdown(data: dict[str, Any]) -> str:
    if not data.get("exists", True) or not data.get("summary"):
        period = data.get("period") or {}
        return "\n".join([
            f"# 周报（{period.get('start_date', '未指定')} 至 {period.get('end_date', '未指定')}）",
            "", data.get("disclaimer") or "尚未生成本周期周报。",
        ]) + "\n"
    summary = data["summary"]
    pending_label = summary.get("pending_label") or "待确认 0 项"
    lines = [
        f"# {data['project_name']} 周报", "",
        f"统计周期：{data['period']['start_date']} 至 {data['period']['end_date']}", "",
        "## 概览",
        f"- 任务总数：{summary['tasks_total']}",
        f"- 本周完成：{summary['tasks_completed']}",
        f"- 进行中：{summary['tasks_in_progress']}",
        f"- 延期：{summary['tasks_overdue']}",
        f"- 打卡次数：{summary['checkin_count']}",
        f"- 打卡工时：{summary.get('checkin_hours', 0)}（有打卡时作为有效工时）",
        f"- 任务工时：{summary.get('task_hours', 0)}（无打卡时作为有效工时）",
        f"- 有效工时：{summary.get('actual_hours', 0)}（打卡优先，不与任务工时相加）",
        f"- 确认贡献：{summary.get('contribution_count', 0)} 项",
        f"- {pending_label}", "", "## 成员产出",
    ]
    members = data.get("members") or []
    if members:
        lines.append("> 不排名，仅展示每人本周事实与产出摘要")
        for member in members:
            member_hours = member.get("checkin_hours", 0) if member.get("hours_source") == "checkin" else member.get("task_hours", 0)
            member_pending = member.get("pending_contribution_count", 0)
            pending_text = f"，待确认 {member_pending} 项" if member_pending else ""
            summary_text = member.get("summary") or f"完成 {member['completed_tasks']} 项任务，确认贡献 {member.get('contribution_count', 0)} 项，工时 {member_hours} 小时{pending_text}"
            lines.append(f"- {member['name']}：{summary_text}（打卡工时 {member.get('checkin_hours', 0)}，任务工时 {member.get('task_hours', 0)}，有效来源 {member.get('hours_source') or 'task'}）")
    else:
        lines.append("- 本周暂无成员产出数据")
    lines.extend(["", "## 完成亮点"])
    lines.extend([f"- {item}" for item in data.get("highlights") or []] or ["- 暂无已完成任务"])
    lines.extend(["", "## 整体洞察", data.get("insight") or "（未生成洞察）", "", "## 风险", *([f"- {item}" for item in data.get("risks") or []] or ["- 暂无明显风险"]), "", "## 下一步", *[f"- {item}" for item in data.get("next_actions") or []], "", data.get("disclaimer") or "", f"生成时间：{data.get('generated_at', '—')}", f"来源：{data.get('source') or 'rule'}"])
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


def internal_task_detail(project_id: int, task_id: int) -> dict[str, Any]:
    """单任务只读详情：标题/状态/负责人/截止/工时/打卡/评价，供 Agent 工具使用。"""
    conn = db(); ensure_project(conn, project_id)
    row = conn.execute(
        "SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.id=? AND t.project_id=? AND t.deleted_at IS NULL",
        (task_id, project_id),
    ).fetchone()
    if not row:
        conn.close()
        return {"task_id": task_id, "found": False, "message": "任务不存在或不属于该项目"}
    task = as_task(row)
    checkins = conn.execute(
        "SELECT COUNT(*) n,COALESCE(SUM(hours),0) hours FROM task_checkins WHERE task_id=? AND project_id=?",
        (task_id, project_id),
    ).fetchone()
    review = conn.execute(
        "SELECT r.quality,r.comment,r.reviewer_id,u.name reviewer_name,r.created_at FROM task_reviews r LEFT JOIN users u ON u.id=r.reviewer_id WHERE r.task_id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    task["checkin_count"] = checkins["n"] or 0
    task["checkin_hours"] = round(checkins["hours"] or 0, 2)
    task["review"] = dict(review) if review else None
    return {"task_id": task_id, "found": True, "task": task}


def internal_project_snapshot(project_id: int) -> dict[str, Any]:
    conn = db(); project = ensure_project(conn, project_id); detail = _project_detail(conn, project, None)
    members = list_members_internal(conn, project_id)
    tasks = [as_task(row) for row in conn.execute("SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.project_id=? AND t.deleted_at IS NULL ORDER BY t.id", (project_id,)).fetchall()]
    conn.close()
    return {"project": detail, "members": members, "tasks": tasks, "report": internal_project_report(project_id), "risks": internal_project_risks(project_id), "load": internal_member_load(project_id)}


def list_members_internal(conn, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT m.user_id,u.name,m.role,u.skills,u.max_concurrent_tasks,u.status,m.joined_at FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND m.status='active' ORDER BY m.joined_at", (project_id,)).fetchall(); result = []
    for row in rows:
        item = dict(row); item["skills"] = json.loads(item["skills"] or "[]"); result.append(item)
    return result

__all__ = ['internal_member_load', 'internal_recommendations', 'recommendations', 'persist_recommendation_record', 'build_recommendation_payload', 'batch_recommendations', 'list_recommendation_history', 'decide_recommendation', 'internal_project_risks', 'internal_project_report', '_week_bounds', 'internal_weekly_report', 'get_weekly_report', 'generate_weekly_report', 'list_weekly_reports', '_weekly_markdown', '_report_markdown', '_simple_pdf_bytes', 'internal_task_detail', 'internal_project_snapshot', 'list_members_internal', 'RECOMMEND_WEIGHTS', 'RECOMMEND_DISCLAIMER']
