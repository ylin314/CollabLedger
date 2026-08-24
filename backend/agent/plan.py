from __future__ import annotations

from dataclasses import dataclass, asdict


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
        return [PlanStep("snapshot", "读取任务、成员、风险和贡献事实")]

    @staticmethod
    def as_dict(steps: list[PlanStep]) -> list[dict[str, str]]:
        return [asdict(step) for step in steps]
