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

    def task_detail(self, project_id: int, task_id: int) -> dict[str, Any]:
        from backend.services.analytics import internal_task_detail

        return internal_task_detail(project_id, task_id)

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

    def run(self, project_id: int, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if tool_name == "recommend":
            return self.recommend(project_id, str(args.get("task_name") or "未命名任务"), args.get("task_type"), float(args.get("estimated_hours") or 1))
        if tool_name == "snapshot":
            return self.snapshot(project_id)
        if tool_name == "task_detail":
            return self.task_detail(project_id, int(args.get("task_id") or 0))
        if tool_name == "risk_detail":
            return self.risk_detail(project_id)
        if tool_name == "weekly_report":
            return self.weekly_report(project_id, args.get("week_start"))
        if tool_name == "member_load":
            return self.member_load(project_id)
        raise ValueError(f"未知 Agent 工具: {tool_name}")
