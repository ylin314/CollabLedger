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
        if len(candidates) > 1:
            return {
                "found": False,
                "ambiguous": True,
                "error": "任务名称匹配到多项，请补充更完整的标题或任务 ID",
                "candidates": [{"id": task.get("id"), "title": task.get("title")} for task in candidates[:10]],
            }
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

    def platform_activity(
        self,
        project_id: int,
        source: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """按平台来源和时间范围聚合真实贡献记录，保留状态分布且不执行任何写操作。"""
        from datetime import date, timedelta

        from backend.db import connect

        allowed_sources = {"github", "feishu", "tencent_doc", "manual"}
        if source and source not in allowed_sources:
            raise ValueError("不支持的平台来源")
        if period not in (None, "this_week"):
            raise ValueError("不支持的时间范围")
        if period == "this_week":
            today = date.today()
            start_date = (today - timedelta(days=today.weekday())).isoformat()
            end_date = (today + timedelta(days=6 - today.weekday())).isoformat()
        for value in (start_date, end_date):
            if value:
                date.fromisoformat(value)
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")

        conn = connect()
        try:
            where = ["c.project_id=?", "c.deleted_at IS NULL"]
            params: list[Any] = [project_id]
            if source:
                where.append("c.source=?")
                params.append(source)
            date_expr = "substr(COALESCE(c.occurred_at,c.created_at),1,10)"
            if start_date:
                where.append(date_expr + ">=?")
                params.append(start_date)
            if end_date:
                where.append(date_expr + "<=?")
                params.append(end_date)
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
            integration_where = ["pi.project_id=?"]
            integration_params: list[Any] = [project_id]
            if source and source != "manual":
                integration_where.append("pi.platform=?")
                integration_params.append(source)
            integration_rows = conn.execute(
                "SELECT pi.id integration_id,pi.platform,pi.enabled,pc.status connection_status,"
                "pc.external_username,pc.last_synced_at FROM project_integrations pi "
                "JOIN platform_connections pc ON pc.id=pi.connection_id WHERE "
                + " AND ".join(integration_where)
                + " ORDER BY pi.id",
                integration_params,
            ).fetchall()
            integrations = []
            for row in integration_rows:
                last_job = conn.execute(
                    "SELECT status,finished_at,error FROM sync_jobs WHERE integration_id=? ORDER BY id DESC LIMIT 1",
                    (row["integration_id"],),
                ).fetchone()
                item = dict(row)
                item["enabled"] = bool(item.get("enabled"))
                item["last_job_status"] = last_job["status"] if last_job else None
                item["last_job_finished_at"] = last_job["finished_at"] if last_job else None
                item["last_job_has_error"] = bool(last_job and last_job["error"])
                integrations.append(item)
        finally:
            conn.close()

        by_source: dict[str, int] = {}
        by_source_status: dict[str, dict[str, int]] = {}
        by_member: dict[str, dict[str, Any]] = {}
        by_status = {"confirmed": 0, "pending": 0, "disputed": 0}
        for row in rows:
            src_name = row["source"] or "manual"
            count = int(row["cnt"] or 0)
            status = str(row["status"] or "pending")
            by_source[src_name] = by_source.get(src_name, 0) + count
            source_status = by_source_status.setdefault(src_name, {"confirmed": 0, "pending": 0, "disputed": 0})
            source_status[status] = source_status.get(status, 0) + count
            by_status[status] = by_status.get(status, 0) + count
            member_key = str(row["user_id"])
            member = by_member.setdefault(
                member_key,
                {
                    "user_id": row["user_id"],
                    "name": row["member_name"] or "未知成员",
                    "count": 0,
                    "quantity": 0.0,
                    "status_counts": {"confirmed": 0, "pending": 0, "disputed": 0},
                },
            )
            member["count"] += count
            member["quantity"] = round(member["quantity"] + float(row["quantity"] or 0), 2)
            member["status_counts"][status] = member["status_counts"].get(status, 0) + count
        return {
            "project_id": project_id,
            "source_filter": source,
            "period": {"kind": period, "start_date": start_date, "end_date": end_date},
            "total": sum(by_source.values()),
            "by_source": by_source,
            "by_source_status": by_source_status,
            "by_status": by_status,
            "by_member": list(by_member.values()),
            "recent": [dict(row) for row in recent],
            "integrations": integrations,
            "rule": "仅聚合已同步入库贡献；confirmed、pending、disputed 分开统计，不把待确认贡献伪装为已确认；连接和同步状态不包含凭据或原始错误；未接入仅代表系统没有可分析数据，不能推断成员在外部平台是否有实际活动。",
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
            return self.platform_activity(project_id, args.get("source"), args.get("period"), args.get("start_date"), args.get("end_date"))
        if tool_name == "member_load":
            return self.member_load(project_id)
        raise ValueError(f"未知 Agent 工具: {tool_name}")
