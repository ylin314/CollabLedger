"""D6 跨项目合作关系与长期任务方向推荐。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.services.profile import build_profile_internal
from backend.services.profile_authorization import get_authorization, is_profile_enabled_for_project


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _task_involvement_clause(alias: str = "t") -> str:
    return (
        f"({alias}.assignee_id=? OR EXISTS (SELECT 1 FROM task_participants tp "
        f"WHERE tp.task_id={alias}.id AND tp.user_id=? AND tp.status IN ('active','left')))"
    )


def build_collaborations(conn, user_id: int) -> dict[str, Any]:
    """只统计双方都授权的未删除共同项目和真实共同任务。"""
    rows = conn.execute(
        """SELECT other.user_id,u.name,p.id project_id,p.updated_at,p.end_date,p.archived_at
           FROM memberships mine
           JOIN memberships other ON other.project_id=mine.project_id AND other.user_id<>mine.user_id
           JOIN projects p ON p.id=mine.project_id AND p.deleted_at IS NULL
           JOIN users u ON u.id=other.user_id
           WHERE mine.user_id=? AND mine.status IN ('active','left')
             AND other.status IN ('active','left')
           ORDER BY other.user_id,p.id""",
        (user_id,),
    ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        project_id = int(row["project_id"])
        other_id = int(row["user_id"])
        if not is_profile_enabled_for_project(conn, user_id, project_id):
            continue
        if not is_profile_enabled_for_project(conn, other_id, project_id):
            continue
        node = grouped.setdefault(
            other_id,
            {"user_id": other_id, "name": row["name"], "projects": [], "shared_tasks": []},
        )
        node["projects"].append(
            {
                "project_id": project_id,
                "last_at": row["end_date"] or row["archived_at"] or row["updated_at"],
            }
        )

    items: list[dict[str, Any]] = []
    for other_id, node in grouped.items():
        seen_tasks: set[int] = set()
        task_rows: list[dict[str, Any]] = []
        for project in node["projects"]:
            rows_for_project = conn.execute(
                f"""SELECT t.id,t.updated_at FROM tasks t
                    WHERE t.project_id=? AND t.deleted_at IS NULL
                      AND {_task_involvement_clause('t')}
                      AND {_task_involvement_clause('t')}""",
                (
                    project["project_id"],
                    user_id, user_id,
                    other_id, other_id,
                ),
            ).fetchall()
            for task in rows_for_project:
                task_id = int(task["id"])
                if task_id in seen_tasks:
                    continue
                seen_tasks.add(task_id)
                task_rows.append({"task_id": task_id, "updated_at": task["updated_at"]})
        dates = [str(project["last_at"]) for project in node["projects"] if project["last_at"]]
        dates.extend(str(task["updated_at"]) for task in task_rows if task["updated_at"])
        shared_project_count = len(node["projects"])
        shared_task_count = len(task_rows)
        score = min(100, 30 * shared_project_count + 5 * min(shared_task_count, 10))
        items.append(
            {
                "user_id": other_id,
                "name": node["name"],
                "shared_project_count": shared_project_count,
                "shared_task_count": shared_task_count,
                "last_collaborated_at": max(dates) if dates else None,
                "cooperation_score": score,
                "source_project_ids": [project["project_id"] for project in node["projects"]],
                "calculation": {
                    "formula": "min(100, 共同项目数×30 + min(共同任务数,10)×5)",
                    "scope": "双方均参与、双方均授权且项目未删除；共同任务要求双方均为负责人或参与者",
                    "personality_inference": False,
                },
            }
        )
    items.sort(
        key=lambda item: (
            -item["cooperation_score"],
            item["last_collaborated_at"] or "",
            item["user_id"],
        )
    )
    return {
        "items": items,
        "generated_at": _now_iso(),
        "calculation_notes": "合作分只表达共同项目/共同任务数量，不代表人格、道德或公开排名。",
    }


def build_long_term_recommendations(conn, user_id: int) -> dict[str, Any]:
    """从授权历史画像生成个人任务方向；无历史时仅使用明确标记的自报技能冷启动。"""
    authorization = get_authorization(conn, user_id)
    if authorization["data_status"] in {"frozen", "deleted"}:
        return {
            "recommendations": [],
            "data_status": authorization["data_status"],
            "generated_at": _now_iso(),
            "message": "跨项目画像已停止使用；启用全局开关或项目白名单后才会生成长期推荐。",
        }
    profile = build_profile_internal(conn, user_id, authorized_only=True)
    recommendations: list[dict[str, Any]] = []
    for skill in profile.get("top_skills") or []:
        sample_count = int(skill.get("sample_count") or 0)
        if sample_count <= 0:
            continue
        detail = []
        if profile.get("average_quality") is not None:
            detail.append(f"质量均值 {profile['average_quality']}/5")
        if profile.get("efficiency") is not None:
            detail.append(f"工时比 {profile['efficiency']}")
        reason_tail = "，".join(detail) if detail else "暂无足够质量/工时样本"
        recommendations.append(
            {
                "skill": skill["skill"],
                "score": int(skill["score"]),
                "reason": f"授权历史中有 {sample_count} 个相关任务证据，{reason_tail}。",
                "sample_count": sample_count,
                "data_sources": ["assigned_tasks", "completed_tasks", "quality_reviews"],
                "source_project_ids": [item["project_id"] for item in profile.get("source_projects") or []],
                "cold_start": False,
            }
        )
    if not recommendations:
        for declared in (profile.get("declared_skills") or [])[:5]:
            recommendations.append(
                {
                    "skill": declared,
                    "score": 50,
                    "reason": "仅来自用户自报技能，尚无历史完成任务证据；作为冷启动方向而非能力结论。",
                    "sample_count": 0,
                    "data_sources": ["self_declared_skills"],
                    "source_project_ids": [],
                    "cold_start": True,
                }
            )
    recommendations.sort(key=lambda item: (-item["score"], -item["sample_count"], item["skill"]))
    return {
        "recommendations": recommendations[:5],
        "data_status": authorization["data_status"],
        "generated_at": _now_iso(),
        "calculation_notes": "历史方向使用真实任务技能强度；冷启动固定 50 分并显式标记，不伪装成历史能力。",
    }


__all__ = ["build_collaborations", "build_long_term_recommendations"]