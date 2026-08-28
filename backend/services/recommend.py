from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

from backend.agent.config import AgentConfig
from backend.agent.llm import LLMClient
from backend.core.context import *


RECOMMEND_WEIGHTS = {"skill": 0.4, "quality": 0.3, "efficiency": 0.2, "load": 0.1}
RECOMMEND_DISCLAIMER = "推荐仅供参考，最终由组长决定。"
NEUTRAL_SCORE = 0.5
SKILL_ONTOLOGY = [
    {"id": "backend", "name": "后端开发", "member": ["后端", "python", "fastapi", "django", "flask", "java", "go", "node", "接口", "api", "鉴权", "服务端", "路由"], "task": ["后端", "接口", "api", "鉴权", "服务端", "路由", "schema", "rest", "登录"]},
    {"id": "frontend", "name": "前端开发", "member": ["前端", "react", "vue", "css", "ui", "javascript", "typescript", "交互"], "task": ["前端", "页面", "交互", "css", "react", "vue", "ui", "登录页"]},
    {"id": "database", "name": "数据库", "member": ["数据库", "sql", "sqlite", "postgres", "mysql", "表结构", "迁移"], "task": ["数据库", "表", "sql", "schema", "存储", "迁移"]},
    {"id": "docs", "name": "文档与答辩", "member": ["文档", "答辩", "ppt", "写作", "markdown", "汇报"], "task": ["文档", "答辩", "ppt", "报告", "说明书", "汇报"]},
    {"id": "test", "name": "测试", "member": ["测试", "pytest", "qa", "验收"], "task": ["测试", "验收", "用例"]},
    {"id": "design", "name": "设计", "member": ["设计", "figma", "原型"], "task": ["设计", "原型", "视觉"]},
]
STATUS_LABELS = {
    "generated": "已生成，待组长确认",
    "accept": "已采纳推荐人选",
    "accepted": "已采纳推荐人选",
    "manual": "已手选其他人",
    "assigned": "已完成指派",
}

def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clip(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def recommend_config() -> dict[str, Any]:
    mode = (os.getenv("RECOMMEND_SKILL_MODE") or "llm").strip().lower()
    if mode not in {"llm", "embedding", "rule"}:
        mode = "llm"
    return {
        "skill_mode": mode,
        "use_llm_skill": _env_bool("RECOMMEND_USE_LLM_SKILL", True) and mode != "rule",
        "use_llm_reason": _env_bool("RECOMMEND_USE_LLM_REASON", True),
        "skill_ai_weight": _clip(os.getenv("RECOMMEND_SKILL_AI_WEIGHT", "0.55")),
        "llm_timeout": float(os.getenv("RECOMMEND_LLM_TIMEOUT") or os.getenv("LLM_TIMEOUT_SECONDS") or "12"),
        "embedding_url": (os.getenv("LLM_EMBEDDING_URL") or "").strip(),
        "embedding_model": (os.getenv("LLM_EMBEDDING_MODEL") or "text-embedding-3-small").strip(),
    }


def _parse_skills(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            values = []
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _member_load(project_id: int) -> dict[str, Any]:
    from backend.services.analytics import internal_member_load
    return internal_member_load(project_id)


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


def rule_skill(
    skills: list[str],
    history_types: list[str],
    history_titles: list[str],
    task_title: str,
    task_type: Optional[str],
    description: str = "",
) -> tuple[float, list[str], list[str], list[dict[str, Any]]]:
    task_blob = " ".join(part for part in (task_type, task_title, description) if part)
    member_blob = " ".join(skills)
    matched = [skill for skill in skills if _normalize(skill) and _normalize(skill) in _normalize(task_blob)]
    families: list[dict[str, Any]] = []
    for spec in SKILL_ONTOLOGY:
        member_hits = _term_hits(member_blob, spec["member"])
        task_hits = _term_hits(task_blob, spec["task"])
        if member_hits and task_hits:
            families.append({"id": spec["id"], "name": spec["name"], "member_hits": member_hits, "task_hits": task_hits})
    type_hits = [item for item in history_types if task_type and _normalize(str(item)) == _normalize(task_type)]
    title_hits = [title for title in history_titles if task_type and _normalize(task_type) in _normalize(title)]
    score = 0.0
    if families:
        score += min(0.75, 0.5 + 0.15 * (len(families) - 1))
    if matched:
        score += 0.2
    if type_hits:
        score += 0.2
    elif title_hits:
        score += 0.12
        type_hits = [task_type] if task_type else []
    if not families and not matched and type_hits:
        score = max(score, 0.45)
    if not families and not matched and not type_hits:
        score = 0.08 if skills else 0.0
    return _clip(score), matched, type_hits, families

def _quality(conn, project_id: int, user_id: int) -> tuple[float, int, bool, float]:
    row = conn.execute(
        """SELECT AVG(COALESCE(r.quality,t.quality)) q, COUNT(COALESCE(r.quality,t.quality)) n
           FROM tasks t LEFT JOIN task_reviews r ON r.task_id=t.id
           WHERE t.project_id=? AND t.assignee_id=? AND t.deleted_at IS NULL
             AND (r.quality IS NOT NULL OR t.quality IS NOT NULL)""",
        (project_id, user_id),
    ).fetchone()
    samples = int(row["n"] or 0)
    if samples <= 0:
        return NEUTRAL_SCORE, 0, True, 0.0
    average = float(row["q"] or 0)
    return _clip(average / 5.0), samples, False, round(average, 2)


def _efficiency(conn, project_id: int, user_id: int) -> tuple[float, int, bool, float]:
    ratios = [
        row["ratio"]
        for row in conn.execute(
            """SELECT CASE WHEN actual_hours>0 THEN estimated_hours * 1.0 / actual_hours END ratio
               FROM tasks WHERE project_id=? AND assignee_id=? AND status='completed' AND deleted_at IS NULL
                 AND estimated_hours IS NOT NULL AND actual_hours IS NOT NULL""",
            (project_id, user_id),
        ).fetchall()
        if row["ratio"] is not None
    ]
    if not ratios:
        return NEUTRAL_SCORE, 0, True, NEUTRAL_SCORE
    raw = sum(ratios) / len(ratios)
    return _clip(min(1.2, raw) / 1.2), len(ratios), False, round(raw, 2)


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        raw = re.sub(r"^" + fence + r"(?:json)?", "", raw, flags=re.IGNORECASE).rstrip(chr(96)).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("LLM 返回的 JSON 不是对象")
    return data


def llm_json(prompt: str, timeout: float) -> dict[str, Any]:
    base = AgentConfig.from_env()
    if not base.configured:
        raise RuntimeError("LLM 未配置")
    client = LLMClient(
        AgentConfig(
            base_url=base.base_url,
            api_key=base.api_key,
            model=base.model,
            timeout_seconds=timeout,
            temperature=base.temperature,
            max_tokens=base.max_tokens,
        )
    )
    content = client.complete(
        [
            {"role": "system", "content": "你是协作账本的任务推荐助手。只根据给定事实打分和写理由，禁止编造经历，禁止排名或负面标签。对低匹配候选人使用中性、建设性表述，不使用“不适合”“弱”“差”。只返回 JSON。"},
            {"role": "user", "content": prompt},
        ]
    )
    return _extract_json(content)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)

def embedding_skill(task: dict[str, Any], candidates: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[dict[int, float], dict[int, str], str, Optional[str]]:
    if not candidates:
        return {}, {}, "rule", None
    agent = AgentConfig.from_env()
    url = cfg["embedding_url"]
    if not url:
        base = (agent.base_url or "").rstrip("/")
        url = f"{base}/embeddings" if base.endswith("/v1") else f"{base}/v1/embeddings"
    if not agent.api_key or not url:
        return {}, {}, "rule", "embedding 未配置"
    texts = [f"任务：{task.get('title') or ''}。类型：{task.get('task_type') or ''}。描述：{(task.get('description') or '')[:400]}"]
    for item in candidates:
        texts.append(
            f"成员：{item['name']}。技能：{'、'.join(item.get('skills') or []) or '未填写'}。"
            f"历史类型：{'、'.join(item.get('history_types') or []) or '无'}。"
            f"历史任务：{'、'.join((item.get('history_titles') or [])[:5]) or '无'}"
        )
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {agent.api_key}", "Content-Type": "application/json"},
            json={"model": cfg["embedding_model"], "input": texts},
            timeout=cfg["llm_timeout"],
        )
        response.raise_for_status()
        vectors = [item.get("embedding") or [] for item in response.json().get("data") or []]
        if len(vectors) != len(texts):
            return {}, {}, "rule", "embedding 数量不匹配"
        scores, notes = {}, {}
        for item, vector in zip(candidates, vectors[1:]):
            similarity = _cosine(vectors[0], vector)
            scores[item["id"]] = _clip((similarity + 1) / 2 if similarity < 0 else similarity)
            notes[item["id"]] = f"语义相似度 {round(scores[item['id']], 2)}"
        return scores, notes, "embedding", None
    except Exception as exc:
        return {}, {}, "rule", str(exc)


def llm_skill(task: dict[str, Any], candidates: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[dict[int, float], dict[int, str], str, Optional[str]]:
    if not candidates or not cfg["use_llm_skill"] or not AgentConfig.from_env().configured:
        return {}, {}, "rule", None
    payload = {
        "task": {"title": task.get("title"), "task_type": task.get("task_type"), "description": (task.get("description") or "")[:400]},
        "candidates": [
            {
                "user_id": item["id"],
                "name": item["name"],
                "skills": item.get("skills") or [],
                "history_types": item.get("history_types") or [],
                "history_titles": (item.get("history_titles") or [])[:5],
            }
            for item in candidates
        ],
    }
    prompt = (
        "根据任务与成员技能、历史任务的语义相关性，为每人打 0 到 1 的 skill 分。只依据事实，不编造。"
        "返回 JSON 对象，含 scores 数组，每项含 user_id、skill、reason。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        data = llm_json(prompt, cfg["llm_timeout"])
        scores, notes = {}, {}
        for item in data.get("scores") or []:
            user_id = int(item["user_id"])
            scores[user_id] = _clip(item.get("skill") or 0)
            if item.get("reason"):
                notes[user_id] = str(item["reason"])
        return scores, notes, "llm", None
    except Exception as exc:
        return {}, {}, "rule", str(exc)


def llm_reasons(task: dict[str, Any], items: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[dict[int, str], str, Optional[str]]:
    if not items or not cfg["use_llm_reason"] or not AgentConfig.from_env().configured:
        return {}, "rule", None
    compact = [
        {
            "user_id": item["user_id"],
            "name": item["name"],
            "score": item["score"],
            "dims": {
                key: {
                    "score": item["dimensions"][key]["score"],
                    "note": item["dimensions"][key]["note"],
                    "samples": item["dimensions"][key].get("samples", 0),
                    "missing": item["dimensions"][key].get("missing", False),
                }
                for key in ("skill", "quality", "efficiency", "load")
            },
        }
        for item in items
    ]
    prompt = (
        "为每位候选人写一句中文推荐理由，先结论后事实，必须引用候选人四维事实数据中的具体数值或样本情况，禁止编造、禁止排名、禁止负面标签。低匹配候选人请说明当前任务与本人技能相关性较低，并指出更匹配的方向；不要使用“不适合”“弱”“差”。"
        "返回 JSON 对象，含 reasons 数组，每项含 user_id 与 summary。\n任务："
        + json.dumps({"title": task.get("title"), "task_type": task.get("task_type"), "description": (task.get("description") or "")[:400]}, ensure_ascii=False)
        + "\n候选人："
        + json.dumps(compact, ensure_ascii=False)
    )
    try:
        data = llm_json(prompt, cfg["llm_timeout"])
        reasons = {
            int(item["user_id"]): str(item.get("summary") or "").strip()
            for item in data.get("reasons") or []
            if item.get("summary")
        }
        return reasons, "llm", None
    except Exception as exc:
        return {}, "rule", str(exc)


def _ai_skill(task: dict[str, Any], candidates: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[dict[int, float], dict[int, str], str, Optional[str]]:
    if cfg["skill_mode"] == "embedding":
        scores, notes, source, error = embedding_skill(task, candidates, cfg)
        if scores:
            return scores, notes, source, error
        fallback = llm_skill(task, candidates, cfg)
        return fallback if fallback[0] else (scores, notes, "rule", error or fallback[3])
    if cfg["skill_mode"] == "llm":
        return llm_skill(task, candidates, cfg)
    return {}, {}, "rule", None


def _dimension(score: float, weight: float, note: str, evidence: list[str], *, samples: int = 0, missing: bool = False, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = {
        "score": round(score, 3),
        "weight": weight,
        "weighted": round(score * weight, 4),
        "note": note,
        "evidence": evidence,
        "samples": samples,
        "missing": missing,
    }
    if extra:
        payload.update(extra)
    return payload

def _profiles(conn, project_id: int, load_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT u.id,u.name,u.skills,u.max_concurrent_tasks,m.role FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id",
        (project_id,),
    ).fetchall()
    profiles = []
    for row in rows:
        history = conn.execute(
            "SELECT title,task_type FROM tasks WHERE project_id=? AND assignee_id=? AND deleted_at IS NULL",
            (project_id, row["id"]),
        ).fetchall()
        profiles.append(
            {
                "id": row["id"],
                "name": row["name"],
                "role": row["role"],
                "skills": _parse_skills(row["skills"]),
                "history_types": sorted({item["task_type"] for item in history if item["task_type"]}),
                "history_titles": [item["title"] for item in history if item["title"]],
                "load": load_by_id.get(row["id"]) or {},
            }
        )
    return profiles


def _score_candidates(project_id: int, task: dict[str, Any], limit: int, include_owner: bool) -> dict[str, Any]:
    cfg = recommend_config()
    load = _member_load(project_id)
    load_by_id = {item["user_id"]: item for item in load["members"]}
    conn = db()
    ensure_project(conn, project_id)
    profiles = _profiles(conn, project_id, load_by_id)
    excluded: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for profile in profiles:
        load_item = profile["load"]
        current = int(load_item.get("current_task_count") or 0)
        maximum = max(1, int(load_item.get("max_concurrent_tasks") or 3))
        if profile["role"] == "viewer":
            excluded.append({"user_id": profile["id"], "name": profile["name"], "role": profile["role"], "reason_code": "viewer", "reason": "只读成员不进入推荐候选"})
            continue
        if profile["role"] == "owner" and not include_owner:
            excluded.append({"user_id": profile["id"], "name": profile["name"], "role": profile["role"], "reason_code": "owner_excluded", "reason": "组长默认不进入推荐候选，避免占用执行名额"})
            continue
        if current >= maximum:
            excluded.append({"user_id": profile["id"], "name": profile["name"], "role": profile["role"], "reason_code": "overloaded", "reason": f"已达并发上限 {current}/{maximum}，暂不推荐新任务"})
            continue
        eligible.append(profile)
    ai_scores, ai_notes, skill_source, skill_error = _ai_skill(task, eligible, cfg)
    items: list[dict[str, Any]] = []
    for profile in eligible:
        load_item = profile["load"]
        current = int(load_item.get("current_task_count") or 0)
        maximum = max(1, int(load_item.get("max_concurrent_tasks") or 3))
        ratio = current / maximum
        load_score = _clip(1 - ratio)
        load_high = (load_item.get("load_level") == "high") or ratio > 0.8
        rule_score, matched, type_hits, families = rule_skill(
            profile["skills"],
            profile["history_types"],
            profile["history_titles"],
            task.get("title") or "",
            task.get("task_type"),
            task.get("description") or "",
        )
        ai_score = ai_scores.get(profile["id"])
        if ai_score is None:
            skill_score, local_skill_source = rule_score, "rule"
        else:
            skill_score = _clip((1 - cfg["skill_ai_weight"]) * rule_score + cfg["skill_ai_weight"] * ai_score)
            local_skill_source = skill_source
        quality_score, quality_samples, quality_missing, quality_raw = _quality(conn, project_id, profile["id"])
        efficiency_score, efficiency_samples, efficiency_missing, efficiency_raw = _efficiency(conn, project_id, profile["id"])
        # D6 画像兜底：当前项目样本不足时，用跨项目历史画像（时间衰减）补分
        hist_quality = hist_efficiency = None
        hist_profile = None
        if quality_missing or efficiency_missing or quality_samples < 2 or efficiency_samples < 2:
            from backend.services.profile import build_profile_internal
            hist_profile = build_profile_internal(conn, profile["id"])
        if hist_profile and (quality_missing or quality_samples < 2):
            avg_q = hist_profile.get("average_quality")
            if avg_q is not None and hist_profile.get("quality_samples"):
                quality_score = _clip(float(avg_q) / 5.0)
                hist_quality = float(avg_q)
                quality_missing = False
        if hist_profile and (efficiency_missing or efficiency_samples < 2):
            avg_eff = hist_profile.get("average_efficiency")
            if avg_eff is not None and hist_profile.get("efficiency_samples"):
                eff_ratio = 1.0 / float(avg_eff)
                efficiency_score = _clip(min(1.2, eff_ratio) / 1.2)
                hist_efficiency = float(avg_eff)
                efficiency_missing = False
        family_names = [item["name"] for item in families]
        skill_note = (
            f"技能族命中 {('、'.join(family_names) if family_names else '无')}"
            + (f"；字面技能 {('、'.join(matched))}" if matched else "")
            + (f"；历史同类任务 {('、'.join(type_hits))}" if type_hits else "")
            + (f"；{ai_notes.get(profile['id'])}" if ai_notes.get(profile["id"]) else "")
        )
        quality_note = ("暂无质量评价，按中性分 0.5 计" if quality_missing else (f"参考历史画像（跨项目）质量 {hist_quality}/5（{hist_profile['quality_samples']} 条评价）" if hist_quality is not None else f"历史质量 {quality_raw}/5（{quality_samples} 条评价）"))
        efficiency_note = ("暂无完成工时，按中性分 0.5 计" if efficiency_missing else (f"参考历史画像（跨项目）工时比 {hist_efficiency}（{hist_profile['efficiency_samples']} 条完成记录）" if hist_efficiency is not None else f"预计/实际工时比 {efficiency_raw}（{efficiency_samples} 条完成记录）"))
        load_note = f"当前负载偏高 {current}/{maximum}，但仍未超过上限" if load_high else f"当前负载 {current}/{maximum}（{load_item.get('load_label') or '正常'}）"
        dimensions = {
            "skill": _dimension(skill_score, RECOMMEND_WEIGHTS["skill"], skill_note, [
                f"成员技能：{('、'.join(profile['skills']) if profile['skills'] else '未填写')}",
                f"任务类型/标题：{task.get('task_type') or task.get('title')}",
                f"技能族：{('、'.join(family_names) if family_names else '未命中')}",
                f"规则匹配分 {round(rule_score, 2)}" + (f"，AI 技能分 {round(ai_score, 2)}" if ai_score is not None else "，本次未使用 AI 技能分"),
            ], extra={"source": local_skill_source, "matched_skills": matched, "skill_families": family_names}),
            "quality": _dimension(quality_score, RECOMMEND_WEIGHTS["quality"], quality_note, [quality_note], samples=quality_samples, missing=quality_missing, extra={"average_quality": quality_raw}),
            "efficiency": _dimension(efficiency_score, RECOMMEND_WEIGHTS["efficiency"], efficiency_note, [efficiency_note], samples=efficiency_samples, missing=efficiency_missing, extra={"efficiency": efficiency_raw}),
            "load": _dimension(load_score, RECOMMEND_WEIGHTS["load"], load_note, [f"进行中占用 {current} / 上限 {maximum}", load_note], extra={"current_load": f"{current}/{maximum}", "load_level": load_item.get("load_level"), "high": load_high}),
        }
        total = 100 * sum(item["weighted"] for item in dimensions.values())
        evidence = [line for dim in dimensions.values() for line in dim["evidence"]]
        if hist_profile and (hist_quality is not None or hist_efficiency is not None):
            evidence.append("部分维度参考跨项目历史画像（D6 长期画像）")

        summary = f"{profile['name']}适合接手「{task.get('title') or '该任务'}」：{skill_note}；{quality_note}；{efficiency_note}；{load_note}。"
        items.append({
            "user_id": profile["id"],
            "name": profile["name"],
            "role": profile["role"],
            "score": round(total, 1),
            "weights": RECOMMEND_WEIGHTS,
            "dimensions": dimensions,
            "reasons": {
                "skill_match": round(skill_score, 2),
                "matched_skills": matched,
                "average_quality": hist_quality if hist_quality is not None else (quality_raw if not quality_missing else None),
                "quality_samples": quality_samples,
                "efficiency": hist_efficiency if hist_efficiency is not None else efficiency_raw,
                "efficiency_samples": efficiency_samples,
                "current_load": f"{current}/{maximum}",
                "load_level": load_item.get("load_level"),
                "summary": summary,
                "evidence": evidence,
            },
            "profile_source": "historical" if (hist_profile and (hist_quality is not None or hist_efficiency is not None)) else "current",
            "source": "hybrid" if ai_score is not None else "rule",
        })
    conn.close()
    items.sort(key=lambda item: (-item["score"], item["user_id"]))
    ranked = items[: max(1, limit)] if items else []
    comparison = None
    if len(ranked) >= 2:
        leader, runner = ranked[0], ranked[1]
        delta = round(leader["score"] - runner["score"], 1)
        comparison = {
            "leader_user_id": leader["user_id"],
            "leader_name": leader["name"],
            "runner_user_id": runner["user_id"],
            "runner_name": runner["name"],
            "score_gap": delta,
            "summary": (
                f"{leader['name']}比{runner['name']}高 {delta} 分，主要因为技能 {leader['dimensions']['skill']['score']:.2f} vs {runner['dimensions']['skill']['score']:.2f}，"
                f"质量 {leader['dimensions']['quality']['score']:.2f} vs {runner['dimensions']['quality']['score']:.2f}。"
            ),
        }
        ranked[0]["reasons"]["contrast"] = comparison["summary"]
    elif ranked:
        comparison = {
            "leader_user_id": ranked[0]["user_id"],
            "leader_name": ranked[0]["name"],
            "runner_user_id": None,
            "runner_name": None,
            "score_gap": None,
            "summary": f"当前只有一名可推荐成员：{ranked[0]['name']}。组长和超负载成员已排除。",
        }
        ranked[0]["reasons"]["contrast"] = comparison["summary"]
    if ranked:
        polished, reason_source, reason_error = llm_reasons(task, ranked, cfg)
        for item in ranked:
            summary = polished.get(item["user_id"]) or item["reasons"]["summary"]
            item["reasons"]["summary"] = summary
            item["reason_source"] = reason_source if item["user_id"] in polished else "rule"
    else:
        reason_source, reason_error = "rule", None
    return {
        "items": ranked,
        "comparison": comparison,
        "excluded": excluded,
        "skill_source": skill_source if any(item["source"] != "rule" for item in ranked) else "rule",
        "reason_source": reason_source,
        "skill_error": skill_error,
        "reason_error": reason_error,
        "include_owner": include_owner,
        "config": {"skill_mode": cfg["skill_mode"], "skill_ai_weight": cfg["skill_ai_weight"]},
    }

def persist_recommendation_record(project_id: int, task_id: Optional[int], task_name: Optional[str], generated_by: Optional[int], payload: dict[str, Any], *, mode: str = "single") -> int:
    conn = db()
    stamp = now_iso()
    source = payload.get("source") or "rule"
    cursor = conn.execute(
        """INSERT INTO recommendations(project_id,task_id,task_name,generated_by,payload,created_at,mode,status,source)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (project_id, task_id, task_name, generated_by, json.dumps(payload, ensure_ascii=False), stamp, mode, "generated", source),
    )
    rec_id = int(cursor.lastrowid)
    conn.execute(
        """INSERT INTO recommendation_events(recommendation_id,project_id,task_id,actor_id,action,selected_user_id,note,payload,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (rec_id, project_id, task_id, generated_by, "generated", None, None, json.dumps({"source": source, "mode": mode}, ensure_ascii=False), stamp),
    )
    conn.commit()
    conn.close()
    return rec_id


def build_recommendation_payload(
    project_id: int,
    task_id: Optional[int],
    task_name: str,
    task_type: Optional[str],
    estimated_hours: float,
    limit: int,
    generated_by: Optional[int] = None,
    include_owner: bool = False,
    description: str = "",
    mode: str = "single",
) -> dict[str, Any]:
    task = {"task_id": task_id, "task_name": task_name, "title": task_name, "task_type": task_type, "estimated_hours": estimated_hours, "description": description}
    scored = _score_candidates(project_id, task, limit, include_owner)
    source = "hybrid" if scored["skill_source"] != "rule" or scored["reason_source"] != "rule" else "rule"
    payload = {
        "task": {"task_id": task_id, "task_name": task_name, "task_type": task_type, "estimated_hours": estimated_hours},
        "recommendations": scored["items"],
        "comparison": scored.get("comparison"),
        "excluded": scored["excluded"],
        "excluded_overloaded": [item for item in scored["excluded"] if item.get("reason_code") == "overloaded"],
        "weights": RECOMMEND_WEIGHTS,
        "disclaimer": RECOMMEND_DISCLAIMER,
        "generated_at": now_iso(),
        "source": source,
        "skill_source": scored["skill_source"],
        "reason_source": scored["reason_source"],
        "include_owner": include_owner,
        "mode": mode,
        "errors": {key: scored[key] for key in ("skill_error", "reason_error") if scored.get(key)},
        "config": scored["config"],
    }
    payload["recommendation_id"] = persist_recommendation_record(project_id, task_id, task_name, generated_by, payload, mode=mode)
    return payload


def internal_recommendations(project_id: int, task_name: str, task_type: Optional[str], estimated_hours: float = 1, limit: int = 3, include_owner: bool = False, description: str = "") -> list[dict[str, Any]]:
    return _score_candidates(project_id, {"title": task_name, "task_type": task_type, "estimated_hours": estimated_hours, "description": description}, limit, include_owner)["items"]


def recommendations(project_id: int, task_name: str, task_type: Optional[str], estimated_hours: float = 1) -> list[dict[str, Any]]:
    return internal_recommendations(project_id, task_name, task_type, estimated_hours)


def unassigned_tasks(project_id: int) -> list[dict[str, Any]]:
    conn = db()
    rows = conn.execute(
        """SELECT t.*,u.name assignee_name FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id
           WHERE t.project_id=? AND t.deleted_at IS NULL AND (t.assignee_id IS NULL OR t.status='unassigned')
           ORDER BY t.id""",
        (project_id,),
    ).fetchall()
    conn.close()
    return [as_task(row) for row in rows]


def batch_recommendations(project_id: int, generated_by: Optional[int], limit: int = 3, include_owner: bool = False) -> dict[str, Any]:
    tasks = unassigned_tasks(project_id)
    items = [
        build_recommendation_payload(
            project_id,
            task["id"],
            task["title"],
            task.get("task_type"),
            task.get("estimated_hours") if task.get("estimated_hours") is not None else 1,
            limit,
            generated_by,
            include_owner=include_owner,
            description=task.get("description") or "",
            mode="batch",
        )
        for task in tasks
    ]
    return {"project_id": project_id, "generated_at": now_iso(), "count": len(items), "disclaimer": RECOMMEND_DISCLAIMER, "items": items}


def list_recommendation_history(project_id: int, task_id: Optional[int] = None, limit: int = 20) -> dict[str, Any]:
    conn = db()
    ensure_project(conn, project_id)
    args: list[Any] = [project_id]
    where = "project_id=?"
    if task_id is not None:
        where += " AND task_id=?"
        args.append(task_id)
    rows = conn.execute(f"SELECT * FROM recommendations WHERE {where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    events = conn.execute("SELECT * FROM recommendation_events WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit * 4)).fetchall()
    conn.close()
    items = []
    for row in rows:
        payload = json.loads(row["payload"] or "{}")
        top = (payload.get("recommendations") or [{}])[0]
        keys = row.keys()
        items.append({
            "id": row["id"],
            "task_id": row["task_id"],
            "task_name": row["task_name"],
            "status": row["status"] if "status" in keys else payload.get("status") or "generated",
            "status_label": STATUS_LABELS.get(row["status"] if "status" in keys else payload.get("status") or "generated", row["status"] if "status" in keys else "generated"),
            "source": row["source"] if "source" in keys else payload.get("source"),
            "mode": row["mode"] if "mode" in keys else payload.get("mode") or "single",
            "accepted_user_id": row["accepted_user_id"] if "accepted_user_id" in keys else None,
            "assigned_user_id": row["assigned_user_id"] if "assigned_user_id" in keys else None,
            "created_at": row["created_at"],
            "top": {"user_id": top.get("user_id"), "name": top.get("name"), "score": top.get("score")},
            "disclaimer": payload.get("disclaimer") or RECOMMEND_DISCLAIMER,
        })
    return {
        "project_id": project_id,
        "generated_at": now_iso(),
        "items": items,
        "events": [
            {
                "id": event["id"],
                "recommendation_id": event["recommendation_id"],
                "task_id": event["task_id"],
                "action": event["action"],
                "selected_user_id": event["selected_user_id"],
                "note": event["note"],
                "created_at": event["created_at"],
            }
            for event in events
        ],
    }


def decide_recommendation(project_id: int, rec_id: int, actor_id: Optional[int], user_id: int, note: Optional[str], request) -> dict[str, Any]:
    conn = db()
    row = conn.execute("SELECT * FROM recommendations WHERE id=? AND project_id=?", (rec_id, project_id)).fetchone()
    if not row:
        conn.close()
        fail(404, "NOT_FOUND", "推荐记录不存在")
    payload = json.loads(row["payload"] or "{}")
    task_id = row["task_id"] or (payload.get("task") or {}).get("task_id")
    if not task_id:
        conn.close()
        fail(409, "CONFLICT", "这条推荐还没有关联任务，无法直接指派")
    keys = row.keys()
    status = row["status"] if "status" in keys else "generated"
    if status in {"accept", "accepted", "assigned", "manual"}:
        conn.close()
        fail(409, "CONFLICT", "这条推荐已经完成指派")
    recommended_ids = {int(item["user_id"]) for item in payload.get("recommendations") or []}
    action = "accept" if user_id in recommended_ids else "manual"
    conn.close()
    from backend.routers.tasks import assign_task
    from backend.schemas import AssignIn
    task = assign_task(int(task_id), AssignIn(assignee_id=user_id, note=note or ("采纳推荐" if action == "accept" else "手工指定负责人")), request)
    stamp = now_iso()
    conn = db()
    conn.execute(
        "UPDATE recommendations SET status=?, accepted_user_id=?, accepted_at=?, assigned_user_id=?, assigned_at=? WHERE id=?",
        (action if action == "accept" else "manual", user_id, stamp, user_id, stamp, rec_id),
    )
    conn.execute(
        """INSERT INTO recommendation_events(recommendation_id,project_id,task_id,actor_id,action,selected_user_id,note,payload,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (rec_id, project_id, int(task_id), actor_id, action, user_id, note, json.dumps({"recommended_ids": sorted(recommended_ids), "changed": action == "manual"}, ensure_ascii=False), stamp),
    )
    conn.commit()
    conn.close()
    return {"recommendation_id": rec_id, "action": action, "changed": action == "manual", "task": task, "disclaimer": RECOMMEND_DISCLAIMER}


__all__ = [
    "RECOMMEND_WEIGHTS",
    "RECOMMEND_DISCLAIMER",
    "internal_recommendations",
    "recommendations",
    "persist_recommendation_record",
    "build_recommendation_payload",
    "batch_recommendations",
    "list_recommendation_history",
    "decide_recommendation",
    "unassigned_tasks",
    "recommend_config",
]
