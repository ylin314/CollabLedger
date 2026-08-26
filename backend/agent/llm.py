from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import AgentConfig


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象（兼容 markdown 围栏与前后杂讯）。"""
    raw = (text or "").strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        raw = re.sub(r"^" + fence + r"(?:json)?", "", raw, flags=re.IGNORECASE).rstrip(chr(96)).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("LLM 返回的 JSON 不是对象")
    return data


class LLMClient:
    """调用 OpenAI Chat Completions 兼容接口。"""

    def __init__(self, config: AgentConfig):
        self.config = config

    def complete(
        self,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        if not self.config.configured:
            raise RuntimeError("LLM_API_KEY 未配置")
        effort = reasoning_effort if reasoning_effort is not None else self.config.reasoning_effort
        for attempt in range(2):
            body: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
            }
            if effort:
                body["reasoning_effort"] = effort
            response = httpx.post(
                self.config.chat_completions_url,
                headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=timeout if timeout is not None else self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            message = ((payload.get("choices") or [{}])[0].get("message") or {})
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            content = str(content or "").strip()
            if content:
                return content
            # 推理模型可能把 token 预算全部耗在 reasoning 上；重试一次并加大预算、降低推理强度。
            had_reasoning = bool(message.get("reasoning"))
            if attempt == 0 and had_reasoning:
                effort = effort or "low"
                max_tokens = max(max_tokens or self.config.max_tokens, 4096)
                continue
            raise RuntimeError(f"LLM 返回缺少 choices.message.content: {json.dumps(payload, ensure_ascii=False)[:500]}")
        raise RuntimeError("LLM 返回缺少 choices.message.content")  # pragma: no cover

    def complete_json(
        self,
        messages: list[dict[str, str]],
        timeout: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """结构化 JSON 决策；解析失败时抛出异常由调用方回退规则。"""
        content = self.complete(messages, timeout, max_tokens=max_tokens)
        return _extract_json(content)