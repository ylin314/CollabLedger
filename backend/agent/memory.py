from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AgentMemory:
    """按项目隔离的轻量长期记忆，保存对话事实而非设备行为。"""

    def __init__(self, db_path):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                session_id TEXT NOT NULL DEFAULT 'default',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_memory_project ON agent_memory(project_id, session_id, id)")
        conn.commit()
        conn.close()

    def append(self, project_id: int, role: str, content: str, session_id: str = "default") -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO agent_memory(project_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (project_id, session_id, role, content[:12000], now_iso()),
        )
        conn.commit()
        conn.close()

    def recent(self, project_id: int, session_id: str = "default", limit: int = 8) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT role, content, created_at FROM agent_memory WHERE project_id=? AND session_id=? ORDER BY id DESC LIMIT ?",
            (project_id, session_id, max(1, min(limit, 30))),
        ).fetchall()
        conn.close()
        return [dict(row) for row in reversed(rows)]

    def clear(self, project_id: int, session_id: str = "default") -> None:
        conn = self._connect()
        conn.execute("DELETE FROM agent_memory WHERE project_id=? AND session_id=?", (project_id, session_id))
        conn.commit()
        conn.close()
