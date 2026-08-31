"""D6 成员长期画像：按授权范围聚合真实项目成果，不写常驻画像缓存。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from backend.core.context import db
from backend.services.profile_authorization import profile_source_project_ids
from backend.services.recommend import SKILL_ONTOLOGY

RECENT_DAYS = 90
OLD_WEIGHT = 0.5

_PROFILE_FIELDS = (
    "user_id", "name", "project_count", "projects_count", "completed_task_count",
    "average_quality", "quality_samples", "efficiency", "average_efficiency",
    "efficiency_samples", "on_time_rate", "on_time_samples", "top_skills",
    "skill_families", "skill_strength", "collaboration_types", "contributions_total",
    "active_months", "declared_skills", "data_sources", "source_projects",
    "calculation_notes", "generated_at", "updated_at",
)


def _parse_skills(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(raw or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _term_hits(blob: str, terms: list[str]) -> list[str]:
    haystack = _normalize(blob)
    hits: list[str] = []
    for term in terms:
        token = _normalize(term)
        if token and token in haystack and term not in hits:
            hits.append(term)
    return hits


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _decay_weight(ts: Any, now: Optional[datetime] = None) -> float:
    parsed = _parse_ts(ts)
    if parsed is None:
        return 1.0
    now = now or datetime.now(timezone.utc)
    return 1.0 if (now - parsed).days <= RECENT_DAYS else OLD_WEIGHT


def _weighted_mean(values: list[float], weights: list[float]) -> Optional[float]:
    if not values:
        return None
    total_weight = sum(weights)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in zip(values, weights)) / total_weight


def _scope(project_ids: list[int]) -> tuple[str, list[int]]:
    if not project_ids:
        return "NULL", []
    return ",".join("?" for _ in project_ids), project_ids


def _clip_value(value: float) -> float:
    return max(0.0, min(1.0, value))


def _source_projects(conn, user_id: int, project_ids: list[int]) -> list[dict[str, Any]]:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT p.id project_id,p.name project_name,p.status project_status,
                   m.status membership_status,p.start_date,p.end_date,p.archived_at
            FROM memberships m JOIN projects p ON p.id=m.project_id
            WHERE m.user_id=? AND p.id IN ({marks}) AND p.deleted_at IS NULL
            ORDER BY COALESCE(p.end_date,p.archived_at,p.updated_at,p.created_at) DESC,p.id DESC""",
        (user_id, *args),
    ).fetchall()
    return [dict(row) for row in rows]


def _skill_profile(conn, user_id: int, skills: list[str], project_ids: list[int]) -> dict[str, Any]:
    """自报技能只作为冷启动标签；历史强度只由真实任务证据计算。"""
    families: dict[str, dict[str, Any]] = {}
    declared_blob = " ".join(skills)
    for spec in SKILL_ONTOLOGY:
        declared_hits = _term_hits(declared_blob, spec["member"])
        if declared_hits:
            families[spec["id"]] = {
                "name": spec["name"], "declared_hits": declared_hits, "occurrences": 0,
                "task_count": 0, "done": 0, "qsum": 0.0, "qsamples": 0,
            }
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT t.id,t.task_type,t.title,t.status,COALESCE(r.quality,t.quality) q
            FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id
            WHERE t.assignee_id=? AND t.deleted_at IS NULL AND t.project_id IN ({marks})""",
        (user_id, *args),
    ).fetchall()
    for row in rows:
        task_blob = " ".join(part for part in (row["task_type"], row["title"]) if part)
        for spec in SKILL_ONTOLOGY:
            hits = _term_hits(task_blob, spec["task"])
            if not hits:
                continue
            family = families.setdefault(
                spec["id"],
                {
                    "name": spec["name"], "declared_hits": [], "occurrences": 0,
                    "task_count": 0, "done": 0, "qsum": 0.0, "qsamples": 0,
                },
            )
            family["occurrences"] += len(hits)
            family["task_count"] += 1
            if row["status"] == "completed":
                family["done"] += 1
            if row["q"] is not None:
                family["qsum"] += float(row["q"])
                family["qsamples"] += 1

    family_list: list[dict[str, Any]] = []
    strength_map: dict[str, float] = {}
    top_skills: list[dict[str, Any]] = []
    for family_id, family in families.items():
        if family["occurrences"] < 2:
            continue
        completion = family["done"] / family["task_count"] if family["task_count"] else 0.0
        average_quality = family["qsum"] / family["qsamples"] if family["qsamples"] else None
        quality_score = _clip_value(average_quality / 5.0) if average_quality is not None else 0.5
        strength = round(_clip_value(completion * quality_score), 3)
        family_list.append(
            {
                "id": family_id,
                "name": family["name"],
                "occurrences": family["occurrences"],
                "task_count": family["task_count"],
                "completed_task_count": family["done"],
                "quality_samples": family["qsamples"],
                "declared_matches": family["declared_hits"],
                "evidence_source": "tasks",
            }
        )
        strength_map[family_id] = strength
        top_skills.append(
            {
                "skill": family["name"],
                "score": round(strength * 100),
                "sample_count": family["task_count"],
                "source": "completed_and_assigned_tasks",
                "cold_start": False,
            }
        )
    family_list.sort(key=lambda item: (-item["occurrences"], item["id"]))
    top_skills.sort(key=lambda item: (-item["score"], -item["sample_count"], item["skill"]))
    return {"skill_families": family_list, "skill_strength": strength_map, "top_skills": top_skills}


def _quality_profile(conn, user_id: int, project_ids: list[int]) -> tuple[Optional[float], int]:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT COALESCE(r.quality,t.quality) q,
                   COALESCE(r.created_at,t.updated_at,t.created_at) ts
            FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id
            WHERE t.assignee_id=? AND t.deleted_at IS NULL AND t.project_id IN ({marks})
              AND (r.quality IS NOT NULL OR t.quality IS NOT NULL)""",
        (user_id, *args),
    ).fetchall()
    values = [float(row["q"]) for row in rows]
    weights = [_decay_weight(row["ts"]) for row in rows]
    return _weighted_mean(values, weights), len(values)


def _efficiency_profile(conn, user_id: int, project_ids: list[int]) -> tuple[Optional[float], int]:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT actual_hours,estimated_hours,updated_at ts
            FROM tasks WHERE assignee_id=? AND status='completed' AND deleted_at IS NULL
              AND project_id IN ({marks}) AND actual_hours IS NOT NULL AND estimated_hours IS NOT NULL
              AND estimated_hours > 0""",
        (user_id, *args),
    ).fetchall()
    weights = [_decay_weight(row["ts"]) for row in rows]
    actual = sum(float(row["actual_hours"]) * weight for row, weight in zip(rows, weights))
    estimated = sum(float(row["estimated_hours"]) * weight for row, weight in zip(rows, weights))
    if estimated <= 0:
        return None, 0
    return round(actual / estimated, 4), len(rows)


def _task_outcomes(conn, user_id: int, project_ids: list[int]) -> dict[str, Any]:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT t.id,t.due_date,t.updated_at,
                   (SELECT MAX(l.at) FROM task_logs l WHERE l.task_id=t.id AND l.action='complete') completed_at
            FROM tasks t
            WHERE t.assignee_id=? AND t.status='completed' AND t.deleted_at IS NULL
              AND t.project_id IN ({marks})""",
        (user_id, *args),
    ).fetchall()
    due_rows = [row for row in rows if row["due_date"]]
    on_time = 0
    for row in due_rows:
        completed_at = row["completed_at"] or row["updated_at"]
        if completed_at and str(completed_at)[:10] <= str(row["due_date"])[:10]:
            on_time += 1
    return {
        "completed_task_count": len(rows),
        "on_time_rate": round(on_time / len(due_rows), 4) if due_rows else None,
        "on_time_samples": len(due_rows),
    }


def _collaboration_types(conn, user_id: int, project_ids: list[int]) -> tuple[list[dict[str, Any]], int]:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT kind,COUNT(*) n FROM contributions
            WHERE user_id=? AND status='confirmed' AND deleted_at IS NULL AND project_id IN ({marks})
            GROUP BY kind ORDER BY n DESC,kind""",
        (user_id, *args),
    ).fetchall()
    total = sum(int(row["n"]) for row in rows)
    items = [
        {"type": row["kind"], "count": int(row["n"]), "ratio": round(int(row["n"]) / total, 4)}
        for row in rows
    ] if total else []
    return items, total


def _activity_months(conn, user_id: int, project_ids: list[int]) -> int:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT created_at ts FROM tasks
            WHERE assignee_id=? AND deleted_at IS NULL AND project_id IN ({marks})
            UNION SELECT occurred_at FROM contributions
            WHERE user_id=? AND status='confirmed' AND deleted_at IS NULL AND project_id IN ({marks})
            UNION SELECT work_date FROM work_logs WHERE user_id=? AND project_id IN ({marks})""",
        (user_id, *args, user_id, *args, user_id, *args),
    ).fetchall()
    months: set[str] = set()
    for row in rows:
        parsed = _parse_ts(row["ts"] or "")
        if parsed:
            months.add(parsed.strftime("%Y-%m"))
        elif row["ts"]:
            months.add(str(row["ts"])[:7])
    return len(months)


def _source_count(conn, table: str, where: str, args: tuple[Any, ...]) -> int:
    return int(conn.execute(f"SELECT COUNT(*) n FROM {table} WHERE {where}", args).fetchone()["n"] or 0)


def _empty_profile(user_id: int, name: str, declared_skills: Optional[list[str]] = None) -> dict[str, Any]:
    stamp = _now_iso()
    return {
        "user_id": user_id,
        "name": name,
        "project_count": 0,
        "projects_count": 0,
        "completed_task_count": 0,
        "average_quality": None,
        "quality_samples": 0,
        "efficiency": None,
        "average_efficiency": None,
        "efficiency_samples": 0,
        "on_time_rate": None,
        "on_time_samples": 0,
        "top_skills": [],
        "skill_families": [],
        "skill_strength": {},
        "collaboration_types": [],
        "contributions_total": 0,
        "active_months": 0,
        "declared_skills": declared_skills or [],
        "data_sources": [],
        "source_projects": [],
        "calculation_notes": {
            "scope": "没有可用的授权项目数据",
            "skills": "自报技能不计作历史完成证据",
            "quality": "近90天权重1.0，更早权重0.5",
            "efficiency": "完成任务实际工时合计除以预估工时合计",
            "on_time_rate": "仅统计设置截止日期的已完成任务",
            "contributions": "只统计 confirmed 贡献",
        },
        "generated_at": stamp,
        "updated_at": stamp,
    }


def build_profile_internal(
    conn,
    user_id: int,
    scope_project_id: Optional[int] = None,
    *,
    self_view: bool = False,
    authorized_only: bool = False,
) -> dict[str, Any]:
    """按访问目的聚合画像；authorized_only 用于用户自己的跨项目分析与长期推荐。"""
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        return {}
    skills = _parse_skills(user["skills"])
    project_ids = profile_source_project_ids(
        conn,
        user_id,
        None if authorized_only else scope_project_id,
        self_view=self_view and not authorized_only,
    )
    if not project_ids:
        return _empty_profile(user_id, user["name"], skills)
    skill = _skill_profile(conn, user_id, skills, project_ids)
    average_quality, quality_samples = _quality_profile(conn, user_id, project_ids)
    average_efficiency, efficiency_samples = _efficiency_profile(conn, user_id, project_ids)
    outcomes = _task_outcomes(conn, user_id, project_ids)
    collaboration_types, contributions_total = _collaboration_types(conn, user_id, project_ids)
    source_projects = _source_projects(conn, user_id, project_ids)
    marks, args = _scope(project_ids)
    work_log_count = _source_count(
        conn, "work_logs", f"user_id=? AND project_id IN ({marks})", (user_id, *args)
    )
    assigned_task_count = _source_count(
        conn, "tasks", f"assignee_id=? AND deleted_at IS NULL AND project_id IN ({marks})", (user_id, *args)
    )
    stamp = _now_iso()
    data_sources = [
        {"source": "source_projects", "count": len(source_projects)},
        {"source": "assigned_tasks", "count": assigned_task_count},
        {"source": "completed_tasks", "count": outcomes["completed_task_count"]},
        {"source": "quality_reviews", "count": quality_samples},
        {"source": "efficiency_tasks", "count": efficiency_samples},
        {"source": "confirmed_contributions", "count": contributions_total},
        {"source": "work_logs", "count": work_log_count},
        {"source": "self_declared_skills", "count": len(skills)},
    ]
    return {
        "user_id": user_id,
        "name": user["name"],
        "project_count": len(source_projects),
        "projects_count": len(source_projects),
        "completed_task_count": outcomes["completed_task_count"],
        "average_quality": round(average_quality, 2) if average_quality is not None else None,
        "quality_samples": quality_samples,
        "efficiency": average_efficiency,
        "average_efficiency": average_efficiency,
        "efficiency_samples": efficiency_samples,
        "on_time_rate": outcomes["on_time_rate"],
        "on_time_samples": outcomes["on_time_samples"],
        "top_skills": skill["top_skills"],
        "skill_families": skill["skill_families"],
        "skill_strength": skill["skill_strength"],
        "collaboration_types": collaboration_types,
        "contributions_total": contributions_total,
        "active_months": _activity_months(conn, user_id, project_ids),
        "declared_skills": skills,
        "data_sources": data_sources,
        "source_projects": source_projects,
        "calculation_notes": {
            "scope": "未删除项目中 active/left 成员记录；跨项目用途再叠加用户全局开关和项目覆盖",
            "skills": "历史技能分只使用真实任务命中、完成状态和质量；自报技能仅作冷启动标签",
            "quality": "任务评价/任务质量加权均值，近90天权重1.0，更早权重0.5",
            "efficiency": "已完成任务的衰减加权实际工时合计除以预估工时合计，越小表示相对预估更快",
            "on_time_rate": "完成日期不晚于截止日期；仅统计设置截止日期的已完成任务",
            "contributions": "只统计 confirmed 且未删除贡献，pending/disputed 不进入画像",
        },
        "generated_at": stamp,
        "updated_at": stamp,
    }


def build_profile(user_id: int) -> dict[str, Any]:
    conn = db()
    try:
        return build_profile_internal(conn, user_id, self_view=True)
    finally:
        conn.close()


def profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: profile.get(key) for key in _PROFILE_FIELDS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "build_profile",
    "build_profile_internal",
    "profile_payload",
    "RECENT_DAYS",
    "OLD_WEIGHT",
]