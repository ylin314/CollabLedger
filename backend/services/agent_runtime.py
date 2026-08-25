from __future__ import annotations

from backend.agent import AgentConfig, AgentRuntime
from backend.core.context import active_db_path

def get_agent_runtime() -> AgentRuntime:
    return AgentRuntime(active_db_path(), AgentConfig.from_env())

__all__ = ['get_agent_runtime']
