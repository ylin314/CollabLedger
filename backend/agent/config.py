from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


# 本地运行自动读取项目根目录 .env；容器中由 Compose 注入的环境变量优先。
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def _endpoint(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return f"{value}/chat/completions"
    return f"{value}/v1/chat/completions"


@dataclass(frozen=True)
class AgentConfig:
    """OpenAI-compatible LLM 配置；密钥只从环境读取，不写入代码。"""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 45.0
    temperature: float = 0.2
    max_tokens: int = 1200
    reasoning_effort: str | None = None

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            base_url=os.getenv("LLM_BASE_URL", "https://aigw.saurlax.com/"),
            api_key=os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "45")),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1200")),
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "").strip() or None,
        )

    @property
    def chat_completions_url(self) -> str:
        explicit = os.getenv("LLM_CHAT_COMPLETIONS_URL", "").strip()
        return explicit or _endpoint(self.base_url)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.chat_completions_url and urlparse(self.chat_completions_url).scheme in {"http", "https"})

    def public_dict(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "chat_completions_url": self.chat_completions_url,
            "model": self.model,
            "configured": self.configured,
        }
