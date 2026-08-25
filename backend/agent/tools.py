from __future__ import annotations

from typing import Any


class AgentTools:
    """Agent 可调用的项目事实工具。

    HTTP 路由负责认证；进入 Agent 后只使用显式内部只读 helper，避免绕过路由或伪造 Request。
    """

    def snapshot(self, project_id: int) -> dict[str, Any]:
        from backend.main import internal_project_snapshot

        return internal_project_snapshot(project_id)

    def recommend(self, project_id: int, task_name: str, task_type: str | None = None, estimated_hours: float = 1) -> dict[str, Any]:
        from backend.main import internal_recommendations

        return {
            "task_name": task_name,
            "task_type": task_type,
            "estimated_hours": estimated_hours,
            "recommendations": internal_recommendations(project_id, task_name, task_type, estimated_hours),
        }

    def run(self, project_id: int, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if tool_name == "recommend":
            return self.recommend(project_id, str(args.get("task_name") or "未命名任务"), args.get("task_type"), float(args.get("estimated_hours") or 1))
        if tool_name == "snapshot":
            return self.snapshot(project_id)
        raise ValueError(f"未知 Agent 工具: {tool_name}")
