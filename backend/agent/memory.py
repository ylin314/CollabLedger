from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from backend.db import connect, initialize

SUMMARY_ROLE = "summary"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def summary_threshold() -> int:
    return max(4, int(os.getenv("AGENT_SUMMARY_THRESHOLD", "8")))


def summary_recent_limit() -> int:
    return max(2, min(30, int(os.getenv("AGENT_SUMMARY_LIMIT", "8"))))


class AgentMemory:
    """按项目隔离的轻量长期记忆，保存对话事实而非设备行为。"""

    def __init__(self, db_path):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self):
        return connect(self.db_path)

    def _ensure_schema(self) -> None:
        initialize(self.db_path)

    def append(self, project_id: int, role: str, content: str, session_id: str = "default") -> None:
        conn = self._connect()
        stamp = now_iso()
        # agent_memory 是兼容旧数据的宽松记忆表；单元测试和内部调用可能没有
        # 对应的 projects 行，因此仅在真实项目存在时维护会话元数据。
        project_exists = conn.execute(
            "SELECT 1 FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if project_exists:
            session = conn.execute(
                "SELECT id FROM agent_sessions WHERE project_id=? AND session_key=?",
                (project_id, session_id),
            ).fetchone()
            if session:
                conn.execute(
                    "UPDATE agent_sessions SET updated_at=? WHERE id=?",
                    (stamp, session["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO agent_sessions(project_id,session_key,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (project_id, session_id, None, stamp, stamp),
                )
        conn.execute(
            "INSERT INTO agent_memory(project_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (project_id, session_id, role, content[:12000], stamp),
        )
        conn.commit()
        conn.close()

    def history(self, project_id: int, session_id: str = "default", limit: int = 30) -> list[dict[str, Any]]:
        """返回当前会话的完整可展示历史（摘要也保留）。"""
        limit = max(1, min(limit, 100))
        conn = self._connect()
        rows = conn.execute(
            "SELECT role, content, created_at FROM agent_memory "
            "WHERE project_id=? AND session_id=? ORDER BY id ASC LIMIT ?",
            (project_id, session_id, limit),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def recent(self, project_id: int, session_id: str = "default", limit: int = 8) -> list[dict[str, Any]]:
        """返回最近非摘要消息；若存在摘要则前插一条 role=summary 的压缩上下文。"""
        limit = max(1, min(limit, 30))
        conn = self._connect()
        summary = conn.execute(
            "SELECT content, created_at FROM agent_memory WHERE project_id=? AND session_id=? AND role=? ORDER BY id DESC LIMIT 1",
            (project_id, session_id, SUMMARY_ROLE),
        ).fetchone()
        rows = conn.execute(
            "SELECT role, content, created_at FROM agent_memory WHERE project_id=? AND session_id=? AND role<>? ORDER BY id DESC LIMIT ?",
            (project_id, session_id, SUMMARY_ROLE, limit),
        ).fetchall()
        conn.close()
        items = [dict(row) for row in reversed(rows)]
        if summary is not None:
            items.insert(0, {"role": SUMMARY_ROLE, "content": summary["content"], "created_at": summary["created_at"]})
        return items

    def summarize_old(self, project_id: int, session_id: str = "default", llm_complete=None) -> bool:
        """把最新摘要之前最旧的 threshold 条消息压缩为一条摘要；LLM 失败时保留原消息不压缩。"""
        threshold = summary_threshold()
        keep = summary_recent_limit()
        conn = self._connect()
        latest_summary_id = conn.execute(
            "SELECT id, content FROM agent_memory WHERE project_id=? AND session_id=? AND role=? ORDER BY id DESC LIMIT 1",
            (project_id, session_id, SUMMARY_ROLE),
        ).fetchone()
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM agent_memory WHERE project_id=? AND session_id=? AND role<>? ORDER BY id ASC",
            (project_id, session_id, SUMMARY_ROLE),
        ).fetchall()
        # 全部非摘要消息超过 threshold+keep 时，压缩最旧的 threshold 条；反复调用直到历史全部收进摘要。
        if len(rows) <= threshold + keep or llm_complete is None:
            conn.close()
            return False
        old_rows = rows[:threshold]
        # 旧摘要一并作为上下文，保证压缩是链式的（不丢上一段摘要的信息）。
        summary_messages = []
        if latest_summary_id is not None:
            summary_messages.append({"role": "system", "content": f"已有会话摘要：{latest_summary_id['content']}"})
        summary_messages += [
            {"role": row["role"] if row["role"] in ("user", "assistant") else "user", "content": row["content"]}
            for row in old_rows
        ]
        try:
            summary = str(
                llm_complete(
                    [{"role": "system", "content": "把下面的协作对话压缩成一条中文摘要，保留：用户意图、涉及的任务/成员/日期、已给出的结论与建议。只输出摘要本身。"},
                     *summary_messages],
                )
            ).strip()
        except Exception:
            conn.close()
            return False
        if not summary:
            conn.close()
            return False
        oldest_id = old_rows[0]["id"]
        newest_id = old_rows[-1]["id"]
        conn.execute(
            "DELETE FROM agent_memory WHERE project_id=? AND session_id=? AND role<>? AND id>=? AND id<=?",
            (project_id, session_id, SUMMARY_ROLE, oldest_id, newest_id),
        )
        if latest_summary_id is not None:
            conn.execute("DELETE FROM agent_memory WHERE id=?", (latest_summary_id["id"],))
        conn.execute(
            "INSERT INTO agent_memory(project_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (project_id, session_id, SUMMARY_ROLE, summary[:12000], old_rows[0]["created_at"]),
        )
        conn.commit()
        conn.close()
        return True

    def clear(self, project_id: int, session_id: str = "default") -> None:
        conn = self._connect()
        conn.execute("DELETE FROM agent_memory WHERE project_id=? AND session_id=?", (project_id, session_id))
        conn.execute("DELETE FROM agent_sessions WHERE project_id=? AND session_key=?", (project_id, session_id))
        conn.commit()
        conn.close()
