from __future__ import annotations

import json
from typing import Any

import httpx

from .config import AgentConfig


class LLMClient:
    """调用 OpenAI Chat Completions 兼容接口。"""

    def __init__(self, config: AgentConfig):
        self.config = config

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.config.configured:
            raise RuntimeError("LLM_API_KEY 未配置")
        response = httpx.post(
            self.config.chat_completions_url,
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
            json={"model": self.config.model, "messages": messages, "temperature": self.config.temperature, "max_tokens": self.config.max_tokens},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not content:
            raise RuntimeError(f"LLM 返回缺少 choices.message.content: {json.dumps(payload, ensure_ascii=False)[:500]}")
        return str(content).strip()
