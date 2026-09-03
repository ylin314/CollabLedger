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
    """按项目和用户隔离的轻量长期记忆，保存对话事实而非设备行为。"""

    def __init__(self, db_path):
        self.db_path = db_path
        self.last_error: str | None = None
        self._ensure_schema()

    def _connect(self):
        return connect(self.db_path)

    def _ensure_schema(self) -> None:
        initialize(self.db_path)

    def append(
        self,
        project_id: int,
        role: str,
        content: str,
        session_id: str = "default",
        user_id: int | None = None,
    ) -> None:
        conn = self._connect()
        stamp = now_iso()
        # agent_memory 是兼容旧数据的宽松记忆表；单元测试和内部调用可能没有
        # 对应的 projects 行，因此仅在真实项目存在时维护会话元数据。
        project_exists = conn.execute(
            "SELECT 1 FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if project_exists:
            if user_id is None:
                session = conn.execute(
                    "SELECT id FROM agent_sessions WHERE project_id=? AND user_id IS NULL AND session_key=?",
                    (project_id, session_id),
                ).fetchone()
            else:
                session = conn.execute(
                    "SELECT id FROM agent_sessions WHERE project_id=? AND user_id=? AND session_key=?",
                    (project_id, user_id, session_id),
                ).fetchone()
            if session:
                conn.execute(
                    "UPDATE agent_sessions SET updated_at=? WHERE id=?",
                    (stamp, session["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO agent_sessions(project_id,user_id,session_key,title,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (project_id, user_id, session_id, None, stamp, stamp),
                )
        conn.execute(
            "INSERT INTO agent_memory(project_id,session_id,role,content,created_at,user_id) VALUES (?,?,?,?,?,?)",
            (project_id, session_id, role, content[:12000], stamp, user_id),
        )
        conn.commit()
        conn.close()

    def history(
        self,
        project_id: int,
        session_id: str = "default",
        limit: int = 30,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """返回当前用户会话的完整可展示历史（摘要也保留）。"""
        limit = max(1, min(limit, 100))
        conn = self._connect()
        user_clause = "user_id IS NULL" if user_id is None else "user_id=?"
        user_args = () if user_id is None else (user_id,)
        rows = conn.execute(
            f"SELECT role, content, created_at FROM agent_memory "
            f"WHERE project_id=? AND session_id=? AND {user_clause} ORDER BY id ASC LIMIT ?",
            (project_id, session_id, *user_args, limit),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def recent(
        self,
        project_id: int,
        session_id: str = "default",
        limit: int = 8,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """返回当前用户最近非摘要消息；若存在摘要则前插压缩上下文。"""
        limit = max(1, min(limit, 30))
        conn = self._connect()
        user_clause = "user_id IS NULL" if user_id is None else "user_id=?"
        user_args = () if user_id is None else (user_id,)
        summary = conn.execute(
            f"SELECT content, created_at FROM agent_memory WHERE project_id=? AND session_id=? AND role=? AND {user_clause} ORDER BY id DESC LIMIT 1",
            (project_id, session_id, SUMMARY_ROLE, *user_args),
        ).fetchone()
        rows = conn.execute(
            f"SELECT role, content, created_at FROM agent_memory WHERE project_id=? AND session_id=? AND role<>? AND {user_clause} ORDER BY id DESC LIMIT ?",
            (project_id, session_id, SUMMARY_ROLE, *user_args, limit),
        ).fetchall()
        conn.close()
        items = [dict(row) for row in reversed(rows)]
        if summary is not None:
            items.insert(0, {"role": SUMMARY_ROLE, "content": summary["content"], "created_at": summary["created_at"]})
        return items

    def summarize_old(
        self,
        project_id: int,
        session_id: str = "default",
        llm_complete=None,
        user_id: int | None = None,
    ) -> bool:
        """压缩当前用户会话；失败保留原消息并提供可观测 warning。"""
        self.last_error = None
        threshold = summary_threshold()
        keep = summary_recent_limit()
        conn = self._connect()
        user_clause = "user_id IS NULL" if user_id is None else "user_id=?"
        user_args = () if user_id is None else (user_id,)
        latest_summary_id = conn.execute(
            f"SELECT id, content FROM agent_memory WHERE project_id=? AND session_id=? AND role=? AND {user_clause} ORDER BY id DESC LIMIT 1",
            (project_id, session_id, SUMMARY_ROLE, *user_args),
        ).fetchone()
        rows = conn.execute(
            f"SELECT id, role, content, created_at FROM agent_memory WHERE project_id=? AND session_id=? AND role<>? AND {user_clause} ORDER BY id ASC",
            (project_id, session_id, SUMMARY_ROLE, *user_args),
        ).fetchall()
        # 全部非摘要消息超过 threshold+keep 时，压缩最旧的 threshold 条。
        if len(rows) <= threshold + keep or llm_complete is None:
            conn.close()
            return False
        old_rows = rows[:threshold]
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
        except Exception as exc:
            self.last_error = str(exc)[:240]
            conn.close()
            return False
        if not summary:
            conn.close()
            return False
        oldest_id = old_rows[0]["id"]
        newest_id = old_rows[-1]["id"]
        conn.execute(
            f"DELETE FROM agent_memory WHERE project_id=? AND session_id=? AND role<>? AND id>=? AND id<=? AND {user_clause}",
            (project_id, session_id, SUMMARY_ROLE, oldest_id, newest_id, *user_args),
        )
        if latest_summary_id is not None:
            conn.execute("DELETE FROM agent_memory WHERE id=?", (latest_summary_id["id"],))
        conn.execute(
            "INSERT INTO agent_memory(project_id,session_id,role,content,created_at,user_id) VALUES (?,?,?,?,?,?)",
            (project_id, session_id, SUMMARY_ROLE, summary[:12000], old_rows[0]["created_at"], user_id),
        )
        conn.commit()
        conn.close()
        return True

    def clear(self, project_id: int, session_id: str = "default", user_id: int | None = None) -> None:
        conn = self._connect()
        if user_id is None:
            conn.execute("DELETE FROM agent_memory WHERE project_id=? AND session_id=? AND user_id IS NULL", (project_id, session_id))
        else:
            conn.execute("DELETE FROM agent_memory WHERE project_id=? AND session_id=? AND user_id=?", (project_id, session_id, user_id))
        conn.commit()
        conn.close()
