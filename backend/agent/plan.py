from __future__ import annotations

import re
from dataclasses import dataclass, asdict

TASK_ID_PATTERN = re.compile(r"(?:任务|task)\s*[#号]?\s*(\d+)", flags=re.IGNORECASE)


@dataclass(frozen=True)
class PlanStep:
    tool: str
    purpose: str


class AgentPlanner:
    """先规划再执行工具，避免让模型直接猜测项目事实。"""

    def build(self, message: str) -> list[PlanStep]:
        """快路径预取：统一先取项目快照；消息出现数字任务 ID 时顺手预取详情。
        其余工具选择交给 LLM 决策循环（它能看到白名单与事实缺口），
        避免关键词误匹配导致预取无关工具拖慢首轮回答。"""
        steps = [PlanStep("snapshot", "读取任务、成员、风险和贡献事实")]
        match = TASK_ID_PATTERN.search(message)
        if match:
            steps.append(PlanStep("task_detail", "按 ID 预取任务详情"))
        return steps

    @staticmethod
    def as_dict(steps: list[PlanStep]) -> list[dict[str, str]]:
        return [asdict(step) for step in steps]
