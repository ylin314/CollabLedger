"""D6 成员长期画像：跨项目聚合技能 / 质量 / 效率 / 贡献。

只读聚合，不建常驻表；数据变化即新画像。供 3 处使用：
1. GET /api/users/{user_id}/profile 接口
2. 推荐器样本不足时兜底（profile_source=historical）
3. （未来）Agent 工具

隐私：只统计任务 / 评价 / 工时 / 已确认贡献，不采集个人设备数据。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.core.context import db
from backend.services.recommend import SKILL_ONTOLOGY

# 时间衰减：近 90 天权重 1.0，更早衰减为 0.5。
RECENT_DAYS = 90
OLD_WEIGHT = 0.5

_PROFILE_FIELDS = (
    "user_id", "name", "skill_families", "skill_strength", "average_quality", "quality_samples",
    "average_efficiency", "efficiency_samples", "contributions_total", "projects_count",
    "active_months", "updated_at",
)


def _parse_skills(raw: Any) -> list[str]:
    import json
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(raw or "[]")
        except (TypeError, ValueError):
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
    """近 90 天 1.0，更早 0.5；解析失败按 1.0 计（不因脏时间丢样本）。"""
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


def _skill_profile(conn, user_id: int, skills: list[str]) -> dict[str, Any]:
    """技能族 + 强度：skills 命中词 + 历史任务 type/title 命中词；出现次数>=2 才进入画像。

    skill_strength = 该族任务完成率 x 该族平均质量(0-5 归一) ；无任务样本时仅用完成率。
    """
    families: dict[str, dict[str, Any]] = {}
    member_blob = " ".join(skills)
    for spec in SKILL_ONTOLOGY:
        hits = _term_hits(member_blob, spec["member"])
        if hits:
            families[spec["id"]] = {
                "name": spec["name"], "member_hits": hits,
                "occurrences": len(hits), "total": 0, "done": 0, "qsum": 0.0, "qsamples": 0,
            }
    rows = conn.execute(
        """SELECT task_type,title,status,
           COALESCE(r.quality, t.quality) q
           FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id
           WHERE t.assignee_id=? AND t.deleted_at IS NULL""",
        (user_id,),
    ).fetchall()
    for row in rows:
        task_blob = " ".join(part for part in (row["task_type"], row["title"]) if part)
        for spec in SKILL_ONTOLOGY:
            hits = _term_hits(task_blob, spec["task"])
            if not hits:
                continue
            fam = families.setdefault(spec["id"], {
                "name": spec["name"], "member_hits": [], "occurrences": 0, "total": 0, "done": 0, "qsum": 0.0, "qsamples": 0,
            })
            fam["occurrences"] += len(hits)
            fam["total"] += 1
            if row["status"] == "completed":
                fam["done"] += 1
            if row["q"] is not None:
                fam["qsum"] += float(row["q"]); fam["qsamples"] += 1

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


def _quality_profile(conn, user_id: int) -> tuple[Optional[float], int]:
    """加权均值 quality（review 优先），时间衰减。"""
    rows = conn.execute(
        """SELECT COALESCE(r.quality, t.quality) q,
                  COALESCE(r.created_at, t.updated_at, t.created_at) ts
           FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id
           WHERE t.assignee_id=? AND t.deleted_at IS NULL
             AND (r.quality IS NOT NULL OR t.quality IS NOT NULL)""",
        (user_id,),
    ).fetchall()
    values = [float(row["q"]) for row in rows]
    weights = [_decay_weight(row["ts"]) for row in rows]
    return _weighted_mean(values, weights), len(values)


def _efficiency_profile(conn, user_id: int) -> tuple[Optional[float], int]:
    """效率 = Σ actual_hours / Σ estimated_hours；<1 比预估快，>1 慢。仅 completed。"""
    rows = conn.execute(
        """SELECT actual_hours, estimated_hours, updated_at ts
           FROM tasks WHERE assignee_id=? AND status='completed' AND deleted_at IS NULL
             AND actual_hours IS NOT NULL AND estimated_hours IS NOT NULL
             AND estimated_hours > 0""",
        (user_id,),
    ).fetchall()
    weights = [_decay_weight(row["ts"]) for row in rows]
    actual = sum(float(row["actual_hours"]) * w for row, w in zip(rows, weights))
    estimated = sum(float(row["estimated_hours"]) * w for row, w in zip(rows, weights))
    if estimated <= 0:
        return None, 0
    return round(actual / estimated, 4), len(rows)


def _activity_months(conn, user_id: int) -> int:
    months: set[str] = set()
    for row in conn.execute(
        """SELECT created_at ts FROM tasks WHERE assignee_id=? AND deleted_at IS NULL
           UNION SELECT occurred_at FROM contributions WHERE user_id=? AND deleted_at IS NULL
           UNION SELECT work_date FROM work_logs WHERE user_id=?""",
        (user_id, user_id, user_id),
    ).fetchall():
        parsed = _parse_ts(row["ts"] or "")
        if parsed:
            months.add(parsed.strftime("%Y-%m"))
    return len(months)


def build_profile_internal(conn, user_id: int) -> dict[str, Any]:
    """纯聚合：给定连接与 user_id，返回画像。用户不存在时返回空（由调用方决定 404）。"""
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        return {}
    name = user["name"]
    skills = _parse_skills(user["skills"])
    skill = _skill_profile(conn, user_id, skills)
    average_quality, quality_samples = _quality_profile(conn, user_id)
    average_efficiency, efficiency_samples = _efficiency_profile(conn, user_id)
    contributions_total = conn.execute(
        "SELECT COUNT(*) n FROM contributions WHERE user_id=? AND status='confirmed' AND deleted_at IS NULL",
        (user_id,),
    ).fetchone()["n"]
    projects_count = conn.execute(
        "SELECT COUNT(DISTINCT project_id) n FROM memberships WHERE user_id=?", (user_id,),
    ).fetchone()["n"]
    return {
        "user_id": user_id,
        "name": name,
        "skill_families": skill["skill_families"],
        "skill_strength": skill["skill_strength"],
        "average_quality": round(average_quality, 2) if average_quality is not None else None,
        "quality_samples": quality_samples,
        "average_efficiency": average_efficiency,
        "efficiency_samples": efficiency_samples,
        "contributions_total": int(contributions_total or 0),
        "projects_count": int(projects_count or 0),
        "active_months": _activity_months(conn, user_id),
        "updated_at": _now_iso(),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_profile(user_id: int) -> dict[str, Any]:
    """开放入口：自开连接完成聚合。用户不存在返回 {}。"""
    conn = db()
    try:
        return build_profile_internal(conn, user_id)
    finally:
        conn.close()


def profile_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """固定字段顺序输出（对齐接口文档）。"""
    return {key: profile.get(key) for key in _PROFILE_FIELDS}


__all__ = ["build_profile", "build_profile_internal", "profile_payload", "RECENT_DAYS", "OLD_WEIGHT"]