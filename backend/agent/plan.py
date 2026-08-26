from __future__ import annotations

import re
from dataclasses import dataclass, asdict

TASK_ID_PATTERN = re.compile(r"(?:任务|task)\s*[#号]?\s*(\d+)")


@dataclass(frozen=True)
class PlanStep:
    tool: str
    purpose: str


class AgentPlanner:
    """先规划再执行工具，避免让模型直接猜测项目事实。"""

    def build(self, message: str) -> list[PlanStep]:
        text = message.lower()
        if any(token in text for token in ("谁", "分配", "推荐", "负责人", "适合")):
            return [PlanStep("snapshot", "读取成员负载与历史事实"), PlanStep("recommend", "计算可解释的负责人候选")]
        if any(token in text for token in ("风险", "延期", "risk")):
            return [PlanStep("snapshot", "读取任务、成员、风险和贡献事实"), PlanStep("risk_detail", "读取项目风险列表并定位最严重项")]
        if any(token in text for token in ("周报", "总结", "summary")):
            return [PlanStep("snapshot", "读取任务、成员、风险和贡献事实"), PlanStep("weekly_report", "读取本周周报")]
        if any(token in text for token in ("负载", "健康", "负荷", "load")):
            return [PlanStep("snapshot", "读取任务、成员、风险和贡献事实"), PlanStep("member_load", "读取成员负载与健康度")]
        if TASK_ID_PATTERN.search(text) or "任务" in text:
            return [PlanStep("snapshot", "读取任务、成员、风险和贡献事实"), PlanStep("task_detail", "读取任务详情")]
        return [PlanStep("snapshot", "读取任务、成员、风险和贡献事实")]

    @staticmethod
    def as_dict(steps: list[PlanStep]) -> list[dict[str, str]]:
        return [asdict(step) for step in steps]