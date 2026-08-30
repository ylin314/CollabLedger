"""历史画像授权的最小两档模型：全局开关 + 项目级白名单/覆盖。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.db import now_iso


RETENTION_RETAINED = "retained"
RETENTION_DELETED = "deleted"


def _ensure_authorization(conn, user_id: int):
    row = conn.execute(
        "SELECT * FROM profile_authorizations WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if row is not None:
        return row
    stamp = now_iso()
    conn.execute(
        "INSERT INTO profile_authorizations(user_id,global_enabled,retention_mode,updated_at) VALUES (?,?,?,?)",
        (user_id, 1, RETENTION_RETAINED, stamp),
    )
    return conn.execute(
        "SELECT * FROM profile_authorizations WHERE user_id=?",
        (user_id,),
    ).fetchone()


def _override(conn, user_id: int, project_id: int):
    return conn.execute(
        "SELECT enabled FROM profile_project_authorizations WHERE user_id=? AND project_id=?",
        (user_id, project_id),
    ).fetchone()


def _data_status(conn, user_id: int, auth=None) -> str:
    auth = auth or _ensure_authorization(conn, user_id)
    if auth["retention_mode"] == RETENTION_DELETED:
        return RETENTION_DELETED
    if bool(auth["global_enabled"]):
        return RETENTION_RETAINED
    enabled_override = conn.execute(
        "SELECT 1 FROM profile_project_authorizations WHERE user_id=? AND enabled=1 LIMIT 1",
        (user_id,),
    ).fetchone()
    return RETENTION_RETAINED if enabled_override else "frozen"


def is_profile_enabled_for_project(conn, user_id: int, project_id: int) -> bool:
    """判断该用户在某个来源项目的历史数据是否允许被跨项目使用。"""
    auth = _ensure_authorization(conn, user_id)
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
    """返回画像聚合可读取的来源项目。

    推荐/他人查看时只允许用户曾参与、且被用户授权的历史项目；当前目标项目
    始终保留，用于同项目事实。本人查看自己的画像时不受分享开关影响。
    """
    rows = conn.execute(
        """SELECT DISTINCT p.id,p.classroom_id
           FROM memberships m JOIN projects p ON p.id=m.project_id
           WHERE m.user_id=? AND m.status IN ('active','left') AND p.deleted_at IS NULL""",
        (user_id,),
    ).fetchall()
    if self_view or target_project_id is None:
        return [int(row["id"]) for row in rows]

    target = conn.execute(
        "SELECT id,classroom_id FROM projects WHERE id=? AND deleted_at IS NULL",
        (target_project_id,),
    ).fetchone()
    if target is None:
        return []
    allowed: list[int] = []
    for row in rows:
        source_id = int(row["id"])
        if source_id == target_project_id:
            allowed.append(source_id)
            continue
        # 当前访问者已经通过目标项目成员权限进入；候选人的历史来源项目
        # 只要其本人曾参与且获得全局/项目授权即可使用。项目白名单负责
        # 精确控制来源项目，避免把“班级池”误当成唯一团队边界。
        if is_profile_enabled_for_project(conn, user_id, source_id):
            allowed.append(source_id)
    return allowed


def get_authorization(conn, user_id: int) -> dict[str, Any]:
    auth = _ensure_authorization(conn, user_id)
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
    return {
        # 兼容 API 契约的三字段；当前产品决策下它们共享同一全局开关。
        "cross_project_profile": data_status != RETENTION_DELETED and global_enabled,
        "collaboration_analysis": data_status != RETENTION_DELETED and global_enabled,
        "history_visible": data_status != RETENTION_DELETED and global_enabled,
        "global_enabled": global_enabled and data_status != RETENTION_DELETED,
        "data_status": data_status,
        "retention_mode": auth["retention_mode"],
        "updated_at": auth["updated_at"],
        "project_overrides": {
            str(row["project_id"]): bool(row["enabled"]) for row in overrides
        },
        "projects": project_items,
    }


def update_authorization(
    conn,
    user_id: int,
    *,
    global_enabled: Optional[bool] = None,
    project_overrides: Optional[Mapping[int, bool]] = None,
) -> dict[str, Any]:
    auth = _ensure_authorization(conn, user_id)
    overrides = project_overrides or {}
    if global_enabled is None and not overrides:
        return get_authorization(conn, user_id)
    for project_id in overrides:
        if not conn.execute(
            "SELECT 1 FROM projects p JOIN memberships m ON m.project_id=p.id WHERE p.id=? AND m.user_id=? AND m.status IN ('active','left') AND p.deleted_at IS NULL",
            (int(project_id), user_id),
        ).fetchone():
            raise ValueError(f"项目 {project_id} 不是该用户的历史项目")
    stamp = now_iso()
    next_global = int(global_enabled) if global_enabled is not None else int(auth["global_enabled"])
    should_restore = next_global == 1 or any(bool(value) for value in overrides.values())
    retention_mode = RETENTION_RETAINED if should_restore else auth["retention_mode"]
    if global_enabled is not None or should_restore:
        conn.execute(
            "UPDATE profile_authorizations SET global_enabled=?,retention_mode=?,deleted_at=NULL,updated_at=? WHERE user_id=?",
            (next_global, retention_mode, stamp, user_id),
        )
    for project_id, enabled in overrides.items():
        conn.execute(
            """INSERT INTO profile_project_authorizations(user_id,project_id,enabled,updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id,project_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (user_id, int(project_id), int(bool(enabled)), stamp),
        )
    return get_authorization(conn, user_id)


def delete_derived_profile_data(conn, user_id: int) -> dict[str, Any]:
    """删除持久化的画像派生/授权快照，不删除团队原始任务和贡献。"""
    _ensure_authorization(conn, user_id)
    stamp = now_iso()
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
            "deleted_derived_records": 0,
            "raw_team_records_preserved": True,
            "message": "已停止画像使用并清除授权快照；团队任务、贡献等原始记录未删除。",
        }
    )
    return payload


__all__ = [
    "RETENTION_RETAINED",
    "RETENTION_DELETED",
    "delete_derived_profile_data",
    "get_authorization",
    "is_profile_enabled_for_project",
    "profile_source_project_ids",
    "update_authorization",
]