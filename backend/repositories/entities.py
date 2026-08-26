from __future__ import annotations

import sqlite3
from typing import Any

from backend.core.errors import fail

def task_row(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT t.*,u.name assignee_name,r.name reviewer_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id LEFT JOIN users r ON r.id=t.reviewer_id WHERE t.id=? AND t.deleted_at IS NULL", (task_id,)).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "任务不存在")
    return row


def contribution_row(conn: sqlite3.Connection, contribution_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT c.*,u.name user_name FROM contributions c JOIN users u ON u.id=c.user_id WHERE c.id=? AND c.deleted_at IS NULL", (contribution_id,)).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "贡献记录不存在")
    return row

__all__ = ['task_row', 'contribution_row']
