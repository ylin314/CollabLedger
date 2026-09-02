from __future__ import annotations

from typing import Any


class AgentTools:
    """Agent 可调用的项目事实工具。

    HTTP 路由负责认证；进入 Agent 后只使用显式内部只读 helper，避免绕过路由或伪造 Request。
    """

    def snapshot(self, project_id: int) -> dict[str, Any]:
        from backend.services.analytics import internal_project_snapshot

        return internal_project_snapshot(project_id)

    def recommend(self, project_id: int, task_name: str, task_type: str | None = None, estimated_hours: float = 1) -> dict[str, Any]:
        from backend.services.analytics import internal_recommendations

        return {
            "task_name": task_name,
            "task_type": task_type,
            "estimated_hours": estimated_hours,
            "recommendations": internal_recommendations(project_id, task_name, task_type, estimated_hours),
        }

    def task_detail(self, project_id: int, task_id: int = 0, task_name: str | None = None) -> dict[str, Any]:
        from backend.services.analytics import internal_project_snapshot, internal_task_detail

        if task_id:
            return internal_task_detail(project_id, int(task_id))
        name = (task_name or "").strip()
        if not name:
            return {"found": False, "error": "需要提供 task_id 或 task_name"}
        snapshot = internal_project_snapshot(project_id)
        normalized = name.lower().replace(" ", "")
        candidates = [
            task
            for task in snapshot.get("tasks") or []
            if normalized and normalized in str(task.get("title") or "").lower().replace(" ", "")
        ]
        if not candidates:
            titles = [str(t.get("title") or "") for t in (snapshot.get("tasks") or [])][:10]
            return {"found": False, "error": "未找到标题包含「" + name + "」的任务", "candidates": titles}
        task = candidates[0]
        detail = internal_task_detail(project_id, int(task.get("id")))
        detail["matched_by"] = "task_name"
        return detail

    def risk_detail(self, project_id: int) -> dict[str, Any]:
        from backend.services.analytics import internal_project_risks

        return internal_project_risks(project_id)

    def weekly_report(self, project_id: int, week_start: str | None = None) -> dict[str, Any]:
        from datetime import date

        from backend.services.analytics import get_weekly_report

        parsed = date.fromisoformat(week_start) if week_start else None
        return get_weekly_report(project_id, week_start=parsed)

    def member_load(self, project_id: int) -> dict[str, Any]:
        from backend.services.analytics import internal_member_load

        return internal_member_load(project_id)

    def platform_activity(self, project_id: int, source: str | None = None) -> dict[str, Any]:
        """按平台来源聚合真实贡献记录，保留状态分布且不执行任何写操作。"""
        from backend.db import connect

        conn = connect()
        try:
            where = ["c.project_id=?", "c.deleted_at IS NULL"]
            params: list[Any] = [project_id]
            if source:
                where.append("c.source=?")
                params.append(source)
            where_sql = " AND ".join(where)
            rows = conn.execute(
                "SELECT c.source,c.user_id,u.name member_name,c.status,COUNT(*) cnt,"
                "COALESCE(SUM(c.quantity),0) quantity FROM contributions c "
                "LEFT JOIN users u ON u.id=c.user_id WHERE " + where_sql + " "
                "GROUP BY c.source,c.user_id,u.name,c.status ORDER BY c.source,c.user_id,c.status",
                params,
            ).fetchall()
            recent = conn.execute(
                "SELECT c.source,c.title,c.quantity,c.status,u.name member_name,c.occurred_at "
                "FROM contributions c LEFT JOIN users u ON u.id=c.user_id WHERE " + where_sql + " "
                "ORDER BY COALESCE(c.occurred_at,c.created_at) DESC LIMIT 5",
                params,
            ).fetchall()
        finally:
            conn.close()

        by_source: dict[str, int] = {}
        by_member: dict[str, dict[str, Any]] = {}
        by_status = {"confirmed": 0, "pending": 0, "disputed": 0}
        for row in rows:
            src_name = row["source"] or "manual"
            count = int(row["cnt"] or 0)
            by_source[src_name] = by_source.get(src_name, 0) + count
            status = str(row["status"] or "pending")
            by_status[status] = by_status.get(status, 0) + count
            member_key = str(row["user_id"])
            member = by_member.setdefault(member_key, {"user_id": row["user_id"], "name": row["member_name"] or "未知成员", "count": 0, "quantity": 0.0})
            member["count"] += count
            member["quantity"] = round(member["quantity"] + float(row["quantity"] or 0), 2)
        return {
            "project_id": project_id,
            "source_filter": source,
            "total": sum(by_source.values()),
            "by_source": by_source,
            "by_status": by_status,
            "by_member": list(by_member.values()),
            "recent": [dict(row) for row in recent],
            "rule": "仅聚合已落库贡献；confirmed、pending、disputed 分开统计，不把待确认贡献伪装为已确认。",
        }

    def run(self, project_id: int, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if tool_name == "recommend":
            return self.recommend(project_id, str(args.get("task_name") or "未命名任务"), args.get("task_type"), float(args.get("estimated_hours") or 1))
        if tool_name == "snapshot":
            return self.snapshot(project_id)
        if tool_name == "task_detail":
            return self.task_detail(project_id, int(args.get("task_id") or 0), args.get("task_name"))
        if tool_name == "risk_detail":
            return self.risk_detail(project_id)
        if tool_name == "weekly_report":
            return self.weekly_report(project_id, args.get("week_start"))
        if tool_name == "platform_activity":
            return self.platform_activity(project_id, args.get("source"))
        if tool_name == "member_load":
            return self.member_load(project_id)
        raise ValueError(f"未知 Agent 工具: {tool_name}")
