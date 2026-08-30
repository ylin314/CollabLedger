from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.auth import hash_password
from backend.core.context import now_iso
from backend.db import initialize


ACCOUNTS = [
    ("组长 rxc", "owner-demo@example.com", "[]", 3, "owner"),
    ("后端同学", "backend-demo@example.com", '["后端", "Python", "FastAPI"]', 3, "member"),
    ("前端同学", "frontend-demo@example.com", '["前端", "React"]', 1, "member"),
    ("文档同学", "docs-demo@example.com", '["文档", "答辩"]', 3, "member"),
    ("只读同学", "viewer-demo@example.com", '["测试"]', 3, "viewer"),
]


def seed(path: Path, password: str = "password-123") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    initialize(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    stamp = now_iso()
    password_hash = hash_password(password)
    ids: dict[str, int] = {}
    for name, email, skills, max_tasks, _role in ACCOUNTS:
        row = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET name=?, skills=?, max_concurrent_tasks=?, status='online', password_hash=?, updated_at=? WHERE id=?",
                (name, skills, max_tasks, password_hash, stamp, row["id"]),
            )
            ids[email] = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (name, email, skills, max_tasks, "online", password_hash, stamp, stamp),
            )
            ids[email] = int(cur.lastrowid)
    owner_id = ids["owner-demo@example.com"]
    project = conn.execute("SELECT id FROM projects WHERE name=? AND deleted_at IS NULL", ("阶段二演示项目",)).fetchone()
    if project:
        project_id = project["id"]
        conn.execute(
            "UPDATE projects SET description=?, owner_id=?, status='active', updated_at=? WHERE id=?",
            ("用来演示任务推荐：技能族、质量、效率、负载四维拆开，超负载排除，采纳留痕。", owner_id, stamp, project_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO projects(name,project_type,description,owner_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("阶段二演示项目", "竞赛项目", "用来演示任务推荐：技能族、质量、效率、负载四维拆开，超负载排除，采纳留痕。", owner_id, "active", stamp, stamp),
        )
        project_id = int(cur.lastrowid)
    for _name, email, _skills, _max, role in ACCOUNTS:
        user_id = ids[email]
        existing = conn.execute("SELECT role FROM memberships WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone()
        if existing:
            conn.execute("UPDATE memberships SET role=?, updated_at=? WHERE project_id=? AND user_id=?", (role, stamp, project_id, user_id))
        else:
            conn.execute(
                "INSERT INTO memberships(project_id,user_id,role,joined_at,updated_at) VALUES (?,?,?,?,?)",
                (project_id, user_id, role, stamp, stamp),
            )
    conn.execute("DELETE FROM recommendation_events WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM recommendations WHERE project_id=?", (project_id,))
    conn.execute("UPDATE tasks SET deleted_at=? WHERE project_id=? AND deleted_at IS NULL", (stamp, project_id))
    tasks = [
        ("占满前端容量", "前端登录页重构和交互打磨，占用前端同学全部并发额度。", "前端", ids["frontend-demo@example.com"], "in_progress", 8, None, None),
        ("已完成后端模块", "完成登录鉴权和成员接口，作为后端同学的高质量样本。", "后端", ids["backend-demo@example.com"], "completed", 4, 3, 5),
        ("已完成文档整理", "整理接口说明和答辩提纲，作为文档同学样本。", "文档", ids["docs-demo@example.com"], "completed", 5, 6, 4),
        ("未分配后端接口", "补齐任务推荐相关后端接口与证据字段，需要 Python / FastAPI 经验。", "后端", None, "unassigned", 6, None, None),
        ("未分配答辩材料", "把推荐系统的四维证据整理成答辩 PPT 和一页说明书。", "文档", None, "unassigned", 3, None, None),
    ]
    task_ids: list[int] = []
    for title, desc, task_type, assignee, status, est, actual, quality in tasks:
        cur = conn.execute(
            """INSERT INTO tasks(project_id,title,description,assignee_id,status,task_type,estimated_hours,actual_hours,quality,priority,created_by,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, title, desc, assignee, status, task_type, est, actual, quality, "medium", owner_id, stamp, stamp),
        )
        task_ids.append(int(cur.lastrowid))
    completed_backend, completed_docs = task_ids[1], task_ids[2]
    conn.execute(
        "INSERT INTO task_reviews(task_id,reviewer_id,quality,comment,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (completed_backend, owner_id, 5, "接口完整，证据清楚。", stamp, stamp),
    )
    conn.execute(
        "INSERT INTO task_reviews(task_id,reviewer_id,quality,comment,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (completed_docs, owner_id, 4, "材料可用，结构清楚。", stamp, stamp),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"db": str(path), "project_id": project_id, "owner": "owner-demo@example.com", "password": password, "unassigned": ["未分配后端接口", "未分配答辩材料"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "collab.db"))
    args = parser.parse_args()
    seed(Path(args.db))