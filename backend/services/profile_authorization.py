"""D6 历史画像授权：默认启用，全局开关 + 项目级覆盖。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.db import now_iso


RETENTION_RETAINED = "retained"
RETENTION_DELETED = "deleted"
RETENTION_FROZEN = "frozen"


def _default_authorization() -> dict[str, Any]:
    return {
        "global_enabled": 1,
        "retention_mode": RETENTION_RETAINED,
        "deleted_at": None,
        "updated_at": None,
    }


def _authorization(conn, user_id: int):
    return conn.execute(
        "SELECT * FROM profile_authorizations WHERE user_id=?",
        (user_id,),
    ).fetchone()


def _ensure_authorization(conn, user_id: int):
    row = _authorization(conn, user_id)
    if row is not None:
        return row
    stamp = now_iso()
    conn.execute(
        "INSERT INTO profile_authorizations(user_id,global_enabled,retention_mode,updated_at) VALUES (?,?,?,?)",
        (user_id, 1, RETENTION_RETAINED, stamp),
    )
    return _authorization(conn, user_id)


def _override(conn, user_id: int, project_id: int):
    return conn.execute(
        "SELECT enabled FROM profile_project_authorizations WHERE user_id=? AND project_id=?",
        (user_id, project_id),
    ).fetchone()


def _data_status(conn, user_id: int, auth=None) -> str:
    auth = auth or _authorization(conn, user_id) or _default_authorization()
    if auth["retention_mode"] == RETENTION_DELETED:
        return RETENTION_DELETED
    if bool(auth["global_enabled"]):
        return RETENTION_RETAINED
    enabled_override = conn.execute(
        "SELECT 1 FROM profile_project_authorizations WHERE user_id=? AND enabled=1 LIMIT 1",
        (user_id,),
    ).fetchone()
    return RETENTION_RETAINED if enabled_override else RETENTION_FROZEN


def is_profile_enabled_for_project(conn, user_id: int, project_id: int) -> bool:
    """判断某来源项目是否获准用于跨项目画像/协作分析；纯读取不隐式落库。"""
    auth = _authorization(conn, user_id) or _default_authorization()
    if auth["retention_mode"] == RETENTION_DELETED:
        return False
    override = _override(conn, user_id, project_id)
    if override is not None:
        return bool(override["enabled"])
    return bool(auth["global_enabled"])


def profile_source_project_ids(
    conn,
    user_id: int,
    target_project_id: Optional[int] = None,
    *,
    self_view: bool = False,
) -> list[int]:
    """返回画像可读取的真实来源项目。

    本人查看可读取自己 active/left 的未删除项目；跨项目分析只读取明确授权项目。
    D1 针对某个当前项目推荐时，当前项目事实始终可用，其他历史项目仍受授权控制。
    """
    rows = conn.execute(
        """SELECT DISTINCT p.id
           FROM memberships m JOIN projects p ON p.id=m.project_id
           WHERE m.user_id=? AND m.status IN ('active','left') AND p.deleted_at IS NULL
           ORDER BY p.id""",
        (user_id,),
    ).fetchall()
    existing = [int(row["id"]) for row in rows]
    if self_view:
        return existing
    if target_project_id is None:
        return [project_id for project_id in existing if is_profile_enabled_for_project(conn, user_id, project_id)]
    target_exists = conn.execute(
        "SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL",
        (target_project_id,),
    ).fetchone()
    if target_exists is None:
        return []
    return [
        project_id
        for project_id in existing
        if project_id == target_project_id or is_profile_enabled_for_project(conn, user_id, project_id)
    ]


def get_authorization(conn, user_id: int) -> dict[str, Any]:
    """读取授权；默认值不产生隐式写操作。"""
    auth = _authorization(conn, user_id) or _default_authorization()
    overrides = conn.execute(
        "SELECT project_id,enabled,updated_at FROM profile_project_authorizations WHERE user_id=? ORDER BY project_id",
        (user_id,),
    ).fetchall()
    projects = conn.execute(
        """SELECT DISTINCT p.id project_id,p.name project_name,p.status,m.status membership_status,
                  pa.enabled project_override
           FROM memberships m JOIN projects p ON p.id=m.project_id
           LEFT JOIN profile_project_authorizations pa
             ON pa.project_id=p.id AND pa.user_id=m.user_id
           WHERE m.user_id=? AND m.status IN ('active','left') AND p.deleted_at IS NULL
           ORDER BY p.updated_at DESC,p.id DESC""",
        (user_id,),
    ).fetchall()
    global_enabled = bool(auth["global_enabled"])
    data_status = _data_status(conn, user_id, auth)
    project_items = []
    for row in projects:
        override = row["project_override"]
        enabled = False if data_status == RETENTION_DELETED else (bool(override) if override is not None else global_enabled)
        project_items.append(
            {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "project_status": row["status"],
                "membership_status": row["membership_status"],
                "override": None if override is None else bool(override),
                "enabled": enabled,
            }
        )
    enabled_globally = data_status != RETENTION_DELETED and global_enabled
    return {
        "cross_project_profile": enabled_globally,
        "collaboration_analysis": enabled_globally,
        "history_visible": enabled_globally,
        "global_enabled": enabled_globally,
        "data_status": data_status,
        "retention_mode": auth["retention_mode"],
        "updated_at": auth["updated_at"],
        "project_overrides": {str(row["project_id"]): bool(row["enabled"]) for row in overrides},
        "projects": project_items,
    }


def update_authorization(
    conn,
    user_id: int,
    *,
    global_enabled: Optional[bool] = None,
    project_overrides: Optional[Mapping[int, Optional[bool]]] = None,
) -> dict[str, Any]:
    auth = _ensure_authorization(conn, user_id)
    overrides = project_overrides or {}
    if global_enabled is None and not overrides:
        return get_authorization(conn, user_id)
    for project_id in overrides:
        if not conn.execute(
            """SELECT 1 FROM projects p JOIN memberships m ON m.project_id=p.id
               WHERE p.id=? AND m.user_id=? AND m.status IN ('active','left') AND p.deleted_at IS NULL""",
            (int(project_id), user_id),
        ).fetchone():
            raise ValueError(f"项目 {project_id} 不是该用户的历史项目")
    stamp = now_iso()
    next_global = int(global_enabled) if global_enabled is not None else int(auth["global_enabled"])
    should_restore = next_global == 1 or any(value is True for value in overrides.values())
    retention_mode = RETENTION_RETAINED if should_restore else auth["retention_mode"]
    if global_enabled is not None or should_restore:
        conn.execute(
            "UPDATE profile_authorizations SET global_enabled=?,retention_mode=?,deleted_at=NULL,updated_at=? WHERE user_id=?",
            (next_global, retention_mode, stamp, user_id),
        )
    for project_id, enabled in overrides.items():
        if enabled is None:
            conn.execute(
                "DELETE FROM profile_project_authorizations WHERE user_id=? AND project_id=?",
                (user_id, int(project_id)),
            )
            continue
        conn.execute(
            """INSERT INTO profile_project_authorizations(user_id,project_id,enabled,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id,project_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (user_id, int(project_id), int(enabled), stamp),
        )
    return get_authorization(conn, user_id)


def delete_derived_profile_data(conn, user_id: int) -> dict[str, Any]:
    """删除画像授权/派生快照，不删除团队原始任务、贡献和工时记录。"""
    _ensure_authorization(conn, user_id)
    stamp = now_iso()
    override_count = conn.execute(
        "SELECT COUNT(*) n FROM profile_project_authorizations WHERE user_id=?",
        (user_id,),
    ).fetchone()["n"]
    conn.execute("DELETE FROM profile_project_authorizations WHERE user_id=?", (user_id,))
    conn.execute(
        """UPDATE profile_authorizations
           SET global_enabled=0,retention_mode=?,deleted_at=?,updated_at=?
           WHERE user_id=?""",
        (RETENTION_DELETED, stamp, stamp, user_id),
    )
    payload = get_authorization(conn, user_id)
    payload.update(
        {
            "deleted_derived_records": int(override_count or 0),
            "raw_team_records_preserved": True,
            "message": "已停止画像使用并删除授权派生数据；团队任务、贡献和工时原始记录按项目权限保留。",
        }
    )
    return payload


__all__ = [
    "RETENTION_RETAINED",
    "RETENTION_DELETED",
    "RETENTION_FROZEN",
    "delete_derived_profile_data",
    "get_authorization",
    "is_profile_enabled_for_project",
    "profile_source_project_ids",
    "update_authorization",
]