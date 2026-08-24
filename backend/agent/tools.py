from __future__ import annotations

from typing import Any


class AgentTools:
    """Agent 可调用的项目事实工具。所有工具只读项目协作数据。"""

    def snapshot(self, project_id: int) -> dict[str, Any]:
        from backend.main import get_project, project_report, project_risks

        project = get_project(project_id)
        return {
            "project": {k: project.get(k) for k in ("id", "name", "project_type", "description", "start_date", "end_date")},
            "members": project.get("members", []),
            "tasks": project.get("tasks", []),
            "report": project_report(project_id),
            "risks": project_risks(project_id),
        }

    def recommend(self, project_id: int, task_name: str, task_type: str | None = None, estimated_hours: float = 1) -> dict[str, Any]:
        from backend.main import recommendations

        return {
            "task_name": task_name,
            "task_type": task_type,
            "estimated_hours": estimated_hours,
            "recommendations": recommendations(project_id, task_name, task_type, estimated_hours),
        }

    def run(self, project_id: int, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if tool_name == "recommend":
            return self.recommend(project_id, str(args.get("task_name") or "未命名任务"), args.get("task_type"), float(args.get("estimated_hours") or 1))
        if tool_name == "snapshot":
            return self.snapshot(project_id)
        raise ValueError(f"未知 Agent 工具: {tool_name}")
