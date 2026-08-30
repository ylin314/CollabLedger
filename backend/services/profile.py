"""D6 成员长期画像：跨项目聚合技能 / 质量 / 效率 / 贡献。

只读聚合，不建常驻画像表；数据变化即新画像。D1 推荐在当前项目样本不足时
调用本模块，但必须先经过 profile_authorization 的来源项目过滤。
"""
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
    "user_id", "name", "skill_families", "skill_strength", "average_quality", "quality_samples",
    "average_efficiency", "efficiency_samples", "contributions_total", "projects_count",
    "active_months", "updated_at",
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
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _decay_weight(ts: Any, now: Optional[datetime] = None) -> float:
    parsed = _parse_ts(ts)
    if parsed is None:
        return 1.0
    now = now or datetime.now(timezone.utc)
    if (now - parsed).days <= RECENT_DAYS:
        return 1.0
    return OLD_WEIGHT


def _weighted_mean(values: list[float], weights: list[float]) -> Optional[float]:
    if not values:
        return None
    total_weight = sum(weights)
    if total_weight <= 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def _scope(project_ids: list[int]) -> tuple[str, list[int]]:
    if not project_ids:
        return "1=0", []
    return ",".join("?" for _ in project_ids), project_ids


def _skill_profile(conn, user_id: int, skills: list[str], project_ids: list[int]) -> dict[str, Any]:
    """技能族 + 强度：users.skills 与授权范围内历史任务共同提供证据。"""
    families: dict[str, dict[str, Any]] = {}
    member_blob = " ".join(skills)
    for spec in SKILL_ONTOLOGY:
        hits = _term_hits(member_blob, spec["member"])
        if hits:
            families[spec["id"]] = {
                "name": spec["name"], "member_hits": hits,
                "occurrences": len(hits), "total": 0, "done": 0, "qsum": 0.0, "qsamples": 0,
            }
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT task_type,title,status,COALESCE(r.quality,t.quality) q
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
            fam = families.setdefault(spec["id"], {
                "name": spec["name"], "member_hits": [], "occurrences": 0, "total": 0,
                "done": 0, "qsum": 0.0, "qsamples": 0,
            })
            fam["occurrences"] += len(hits)
            fam["total"] += 1
            if row["status"] == "completed":
                fam["done"] += 1
            if row["q"] is not None:
                fam["qsum"] += float(row["q"])
                fam["qsamples"] += 1

    family_list: list[dict[str, Any]] = []
    strength_map: dict[str, float] = {}
    for family_id, fam in families.items():
        if fam["occurrences"] < 2:
            continue
        completion = (fam["done"] / fam["total"]) if fam["total"] else (1.0 if fam["member_hits"] else 0.0)
        avg_quality = (fam["qsum"] / fam["qsamples"]) if fam["qsamples"] else None
        quality_score = _clip_value((avg_quality or 5) / 5.0) if avg_quality is not None else 0.5
        strength = round(_clip_value(completion * quality_score), 3)
        family_list.append({"id": family_id, "name": fam["name"], "occurrences": fam["occurrences"]})
        strength_map[family_id] = strength
    family_list.sort(key=lambda item: (-item["occurrences"], item["id"]))
    return {"skill_families": family_list, "skill_strength": strength_map}


def _clip_value(value: float) -> float:
    return max(0.0, min(1.0, value))


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
    actual = sum(float(row["actual_hours"]) * w for row, w in zip(rows, weights))
    estimated = sum(float(row["estimated_hours"]) * w for row, w in zip(rows, weights))
    if estimated <= 0:
        return None, 0
    return round(actual / estimated, 4), len(rows)


def _activity_months(conn, user_id: int, project_ids: list[int]) -> int:
    marks, args = _scope(project_ids)
    rows = conn.execute(
        f"""SELECT created_at ts FROM tasks WHERE assignee_id=? AND deleted_at IS NULL AND project_id IN ({marks})
            UNION SELECT occurred_at FROM contributions WHERE user_id=? AND deleted_at IS NULL AND project_id IN ({marks})
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


def build_profile_internal(
    conn,
    user_id: int,
    scope_project_id: Optional[int] = None,
    *,
    self_view: bool = False,
) -> dict[str, Any]:
    """给定连接和访问项目返回画像；跨项目读取严格受授权来源项目过滤。"""
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        return {}
    project_ids = profile_source_project_ids(conn, user_id, scope_project_id, self_view=self_view)
    skills = _parse_skills(user["skills"])
    skill = _skill_profile(conn, user_id, skills, project_ids)
    average_quality, quality_samples = _quality_profile(conn, user_id, project_ids)
    average_efficiency, efficiency_samples = _efficiency_profile(conn, user_id, project_ids)
    marks, args = _scope(project_ids)
    contributions_total = conn.execute(
        f"SELECT COUNT(*) n FROM contributions WHERE user_id=? AND status='confirmed' AND deleted_at IS NULL AND project_id IN ({marks})",
        (user_id, *args),
    ).fetchone()["n"]
    projects_count = len(project_ids)
    return {
        "user_id": user_id,
        "name": user["name"],
        "skill_families": skill["skill_families"],
        "skill_strength": skill["skill_strength"],
        "average_quality": round(average_quality, 2) if average_quality is not None else None,
        "quality_samples": quality_samples,
        "average_efficiency": average_efficiency,
        "efficiency_samples": efficiency_samples,
        "contributions_total": int(contributions_total or 0),
        "projects_count": projects_count,
        "active_months": _activity_months(conn, user_id, project_ids),
        "updated_at": _now_iso(),
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


__all__ = ["build_profile", "build_profile_internal", "profile_payload", "RECENT_DAYS", "OLD_WEIGHT"]