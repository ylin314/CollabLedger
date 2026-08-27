from __future__ import annotations

import json
import os
import re
from typing import Any

from .config import AgentConfig
from .llm import LLMClient
from .memory import AgentMemory
from .plan import AgentPlanner
from .tools import AgentTools


class AgentRuntime:
    """Tool → Memory → Plan → LLM 的统一编排入口（ReAct 简化版多步循环）。"""

    TOOL_WHITELIST = {"snapshot", "recommend", "task_detail", "risk_detail", "weekly_report", "member_load"}

    def __init__(self, db_path, config: AgentConfig | None = None):
        self.config = config or AgentConfig.from_env()
        self.memory = AgentMemory(db_path)
        self.planner = AgentPlanner()
        self.tools = AgentTools()
        self.llm = LLMClient(self.config)

    def _tool_args(self, message: str) -> dict[str, Any]:
        match = re.search(r"[\u2018\u2019\u201c\u201d\'\"]([^\u2018\u2019\u201c\u201d\'\"]{2,80})[\u2018\u2019\u201c\u201d\'\"]", message)
        if match:
            return {"task_name": match.group(1)}
        return {"task_name": message.replace("推荐", "").replace("分配", "").strip()}

    def _run_tool(self, project_id: int, tool_name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        try:
            return self.tools.run(project_id, tool_name, args), None
        except Exception as exc:
            return {"tool": tool_name, "error": str(exc)}, str(exc)

    def _fallback(self, message: str, facts: dict[str, Any]) -> str:
        risks = facts.get("risks", {}).get("risks", [])
        report = facts.get("report", {}).get("overall", {})
        load = facts.get("load", {}).get("members", [])
        recommendation = facts.get("recommendation") or {}
        task_detail = facts.get("task_detail") or {}
        if task_detail.get("found"):
            task = task_detail.get("task") or {}
            return f"任务「{task.get('title')}」当前状态：{task.get('status')}，负责人：{task.get('assignee_name') or '未分配'}，截止日期：{task.get('due_date') or '未设置'}，实际工时：{task.get('actual_hours') or 0} 小时。"
        if any(token in message.lower() for token in ("风险", "延期", "risk")):
            if risks:
                first = risks[0]
                focus = first.get("message") or first.get("title") or "请查看风险列表"
                return f"当前共有 {len(risks)} 个项目风险。优先关注：{focus}。规则：{first.get('rule') or '延期、临近截止、无负责人、高负载'}。"
            return "目前未发现明显项目风险。"
        if any(token in message.lower() for token in ("周报", "总结", "summary")):
            return f"本项目共 {report.get('tasks_total', 0)} 项任务，已完成 {report.get('tasks_completed', 0)} 项，延期 {report.get('tasks_overdue', 0)} 项。以上数字来自任务表，不虚构事实。"
        if recommendation.get("recommendations"):
            top = recommendation["recommendations"][0]
            return f"更适合的候选人是 {top['name']}（匹配度 {top['score']}）。{top.get('reasons', {}).get('summary', '')}推荐仅供参考，最终由组长决定。"
        if load:
            high = [item["name"] for item in load if item.get("load_level") == "high"]
            if high:
                return f"当前高负载成员：{'、'.join(high)}。超负载成员不会进入推荐名单。"
        return "我已读取项目事实。可以继续询问风险、周报，或给出带任务名称的负责人推荐请求。"

    @staticmethod
    def _extract_citations(facts: dict[str, Any]) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        seen: set[tuple[str, Any]] = set()

        def add(item: dict[str, Any]) -> None:
            key = (item.get("type"), item.get("task_id") or item.get("user_id") or item.get("period_start") or item.get("message"))
            if key in seen:
                return
            seen.add(key)
            citations.append(item)

        for task in facts.get("tasks") or []:
            add({"type": "task", "task_id": task.get("id"), "title": task.get("title"), "status": task.get("status")})
        for risk in facts.get("risks", {}).get("risks") or []:
            add({"type": "risk", "message": risk.get("message"), "level": risk.get("level")})
        for member in facts.get("members") or []:
            add({"type": "member", "user_id": member.get("user_id") or member.get("id"), "name": member.get("name")})
        for member in facts.get("load", {}).get("members") or []:
            add({"type": "member", "user_id": member.get("user_id"), "name": member.get("name"), "load_level": member.get("load_level")})
        task_detail = facts.get("task_detail") or {}
        if task_detail.get("found"):
            task = task_detail.get("task") or {}
            add({"type": "task", "task_id": task.get("id"), "title": task.get("title"), "status": task.get("status")})
        recommendation = facts.get("recommendation") or {}
        if recommendation.get("recommendations"):
            add({"type": "recommendation", "task_name": recommendation.get("task_name"), "top": recommendation["recommendations"][0].get("name")})
        weekly = facts.get("weekly_report") or {}
        if weekly.get("period"):
            add({"type": "weekly_report", "period_start": weekly["period"].get("week_start") or weekly["period"].get("start_date"), "source": weekly.get("source")})
        return citations

    def run(self, project_id: int, message: str, session_id: str = "default") -> dict[str, Any]:
        plan = self.planner.build(message)
        facts: dict[str, Any] = {}
        tool_trace: list[dict[str, Any]] = []
        for step in plan:
            if step.tool == "snapshot":
                result, err = self._run_tool(project_id, "snapshot", {})
                if err is None and isinstance(result, dict):
                    facts = {**facts, **result}
                tool_trace.append({"tool": "snapshot", "args": {}, "ok": err is None, "error": err})
            elif step.tool == "recommend":
                args = self._tool_args(message)
                result, err = self._run_tool(project_id, "recommend", args)
                if err is None:
                    facts["recommendation"] = result
                tool_trace.append({"tool": "recommend", "args": args, "ok": err is None, "error": err})
            elif step.tool == "risk_detail":
                result, err = self._run_tool(project_id, "risk_detail", {})
                if err is None:
                    facts["risks"] = result
                tool_trace.append({"tool": "risk_detail", "args": {}, "ok": err is None, "error": err})
            elif step.tool == "weekly_report":
                result, err = self._run_tool(project_id, "weekly_report", {})
                if err is None:
                    facts["weekly_report"] = result
                tool_trace.append({"tool": "weekly_report", "args": {}, "ok": err is None, "error": err})
            elif step.tool == "member_load":
                result, err = self._run_tool(project_id, "member_load", {})
                if err is None:
                    facts["load"] = result
                tool_trace.append({"tool": "member_load", "args": {}, "ok": err is None, "error": err})
            elif step.tool == "task_detail":
                match = re.search(r"(?:任务|task)\s*[#号]?\s*(\d+)", message, flags=re.IGNORECASE)
                args = {"task_id": int(match.group(1))} if match else {}
                result, err = self._run_tool(project_id, "task_detail", args)
                if err is None:
                    facts["task_detail"] = result
                tool_trace.append({"tool": "task_detail", "args": args, "ok": err is None, "error": err})

        history = self.memory.recent(project_id, session_id)
        self.memory.append(project_id, "user", message, session_id)
        memory_messages = [
            {"role": "system" if item["role"] == "summary" else item["role"], "content": item["content"]}
            for item in history
        ]
        system = (
            "你是协作账本 Agent。你只能依据提供的项目事实回答。你不是监控器，不判断成员是否摸鱼，不公开排名。"
            "请用中文，先给结论，再给事实依据和下一步建议；若事实不足要明确说不足。推荐仅供参考，最终由组长决定。"
            "每次回复必须是 JSON 对象：需要更多事实时返回 {\"action\": \"tool\", \"tool\": \"<白名单工具>\", \"args\": {...}}；"
            "可以回答时返回 {\"action\": \"answer\", \"answer\": \"<中文回答>\"}。"
            f"白名单工具：{sorted(self.TOOL_WHITELIST)}。禁止编造数字，禁止输出排名或人格评价。"
        )
        max_steps = max(1, int(os.getenv("AGENT_MAX_STEPS", "4")))
        llm_error: str | None = None
        answer: str | None = None
        source = "fallback"
        for _ in range(max_steps):
            user_payload = {
                "message": message,
                "plan": AgentPlanner.as_dict(plan),
                "facts": facts,
                "tool_trace": tool_trace,
                "recent_memory": history,
            }
            try:
                decision = self.llm.complete_json(
                    [{"role": "system", "content": system}, *memory_messages, {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
                    timeout=self.config.timeout_seconds,
                    max_tokens=max(4096, self.config.max_tokens),
                )
            except Exception as exc:
                llm_error = str(exc)
                break
            action = decision.get("action")
            if action == "answer":
                answer = str(decision.get("answer") or "").strip()
                if answer:
                    source = "llm"
                    break
                llm_error = "LLM 返回空答案"
                break
            if action == "tool":
                tool_name = str(decision.get("tool") or "")
                args = decision.get("args") or {}
                if tool_name not in self.TOOL_WHITELIST:
                    llm_error = f"LLM 请求了白名单外工具: {tool_name}"
                    break
                result, err = self._run_tool(project_id, tool_name, args)
                if err is None and isinstance(result, dict):
                    if tool_name == "recommend":
                        facts["recommendation"] = result
                    elif tool_name == "task_detail":
                        facts["task_detail"] = result
                    elif tool_name == "weekly_report":
                        facts["weekly_report"] = result
                    elif tool_name == "risk_detail":
                        facts["risks"] = result
                    elif tool_name == "member_load":
                        facts["load"] = result
                    elif tool_name == "snapshot":
                        facts = {**facts, **result}
                tool_trace.append({"tool": tool_name, "args": args, "ok": err is None, "error": err})
                continue
            llm_error = f"LLM 决策缺少 action: {decision}"
            break
        if answer is None:
            answer = self._fallback(message, facts)
            source = "fallback"
        self.memory.append(project_id, "assistant", answer, session_id)
        try:
            self.memory.summarize_old(project_id, session_id, llm_complete=self.llm.complete)
        except Exception:
            pass
        return {
            "answer": answer,
            "source": source,
            "llm_error": llm_error,
            "plan": AgentPlanner.as_dict(plan),
            "tool_trace": tool_trace,
            "citations": self._extract_citations(facts),
            "facts": facts,
            "memory": self.memory.recent(project_id, session_id),
        }