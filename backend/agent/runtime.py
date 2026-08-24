from __future__ import annotations

import json
import re
from typing import Any

from .config import AgentConfig
from .llm import LLMClient
from .memory import AgentMemory
from .plan import AgentPlanner
from .tools import AgentTools


class AgentRuntime:
    """Tool → Memory → Plan → LLM 的统一编排入口。"""

    def __init__(self, db_path, config: AgentConfig | None = None):
        self.config = config or AgentConfig.from_env()
        self.memory = AgentMemory(db_path)
        self.planner = AgentPlanner()
        self.tools = AgentTools()
        self.llm = LLMClient(self.config)

    def _tool_args(self, message: str) -> dict[str, Any]:
        match = re.search(r"[‘'“\"]([^’'”\"]{2,80})[’'”\"]", message)
        if match:
            return {"task_name": match.group(1)}
        return {"task_name": message.replace("推荐", "").replace("分配", "").strip()}

    def _fallback(self, message: str, facts: dict[str, Any]) -> str:
        risks = facts.get("risks", {}).get("risks", [])
        report = facts.get("report", {}).get("overall", {})
        if any(token in message.lower() for token in ("风险", "延期", "risk")):
            return f"当前共有 {len(risks)} 个延期或存在风险的任务。" + (f"优先关注：{risks[0]['title']}。" if risks else "目前未发现明显延期任务。")
        if any(token in message.lower() for token in ("周报", "总结", "summary")):
            return f"本项目共 {report.get('tasks', 0)} 项任务，已完成 {report.get('completed', 0)} 项；这是一份基于项目事实的周报摘要。"
        return "我已读取项目事实。可以继续询问风险、周报，或给出带任务名称的负责人推荐请求。"

    def run(self, project_id: int, message: str, session_id: str = "default") -> dict[str, Any]:
        plan = self.planner.build(message)
        facts: dict[str, Any] = {}
        for step in plan:
            if step.tool == "snapshot":
                facts = self.tools.run(project_id, "snapshot")
            elif step.tool == "recommend":
                facts["recommendation"] = self.tools.run(project_id, "recommend", self._tool_args(message))

        history = self.memory.recent(project_id, session_id)
        self.memory.append(project_id, "user", message, session_id)
        system = (
            "你是协作账本 Agent。你只能依据提供的项目事实回答。你不是监控器，不判断成员是否摸鱼，不公开排名。"
            "请用中文，先给结论，再给事实依据和下一步建议；若事实不足要明确说不足。"
        )
        user_payload = {"message": message, "plan": AgentPlanner.as_dict(plan), "facts": facts, "recent_memory": history}
        llm_error = None
        try:
            answer = self.llm.complete([{"role": "system", "content": system}, *history, {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}])
            source = "llm"
        except Exception as exc:
            llm_error = str(exc)
            answer = self._fallback(message, facts)
            source = "fallback"
        self.memory.append(project_id, "assistant", answer, session_id)
        return {"answer": answer, "source": source, "llm_error": llm_error, "plan": AgentPlanner.as_dict(plan), "facts": facts, "memory": self.memory.recent(project_id, session_id)}
