from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse, Response

from backend.auth import COOKIE_NAME, create_session, hash_password, iso_utc, revoke_session, verify_password
from backend.core.context import *
from backend.schemas import *
from backend.services.profile import build_profile_internal, profile_payload
from backend.services.profile_authorization import delete_derived_profile_data, get_authorization, update_authorization
from backend.services.collaboration_profile import build_collaborations, build_long_term_recommendations

router = APIRouter()

@router.post("/api/auth/register", status_code=201)
def register(payload: RegisterIn) -> dict[str, Any]:
    if not payload.name.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "用户名称不能为空"}])
    if not _email_ok(payload.email):
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "email", "message": "邮箱格式不正确"}])
    conn = db()
    if conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (payload.email.strip(),)).fetchone():
        conn.close()
        fail(409, "CONFLICT", "邮箱已被注册")
    stamp = now_iso()
    cur = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (payload.name.strip(), payload.email.strip().lower(), "[]", 3, "offline", hash_password(payload.password), stamp, stamp),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return public_user(row)


@router.post("/api/auth/login")
def login(payload: LoginIn, response: Response, request: Request) -> dict[str, Any]:
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (payload.email.strip(),)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        conn.close()
        fail(401, "UNAUTHORIZED", "邮箱或密码错误")
    token, expires_at = create_session(conn, row["id"])
    conn.execute("UPDATE users SET status='online',updated_at=? WHERE id=?", (now_iso(), row["id"]))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    conn.close()
    secure_setting = os.getenv("COLLAB_COOKIE_SECURE", "auto").lower()
    secure = secure_setting == "true" or (secure_setting == "auto" and request.url.scheme == "https")
    response.set_cookie(
        COOKIE_NAME, token, max_age=7 * 24 * 3600, expires=expires_at,
        httponly=True, secure=secure, samesite="lax", path="/",
    )
    return {"user": public_user(row)}


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    conn = db()
    user = require_user(conn, request)
    revoke_session(conn, request)
    conn.execute("UPDATE users SET status='offline',updated_at=? WHERE id=?", (now_iso(), user["id"]))
    conn.commit()
    conn.close()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax")
    response.status_code = 204
    return response


@router.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    conn = db()
    user = require_user(conn, request)
    out = public_user(user)
    conn.close()
    return out


@router.patch("/api/users/me")
def update_me(payload: UserUpdate, request: Request) -> dict[str, Any]:
    if payload.name is not None and not payload.name.strip():
        fail(422, "VALIDATION_ERROR", "请求参数不正确", [{"field": "name", "message": "用户名称不能为空"}])
    conn = db()
    user = require_user(conn, request)
    data = _dump(payload)
    if data:
        if "skills" in data:
            data["skills"] = json.dumps(data["skills"], ensure_ascii=False)
        data["updated_at"] = now_iso()
        sets = ",".join(f"{key}=?" for key in data)
        conn.execute(f"UPDATE users SET {sets} WHERE id=?", (*data.values(), user["id"]))
        conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    conn.close()
    return public_user(row)


@router.post("/api/users", status_code=201)
def create_user(payload: UserIn, request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    conn = db()
    if request is not None:
        require_user(conn, request)
    if payload.email and conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (payload.email,)).fetchone():
        conn.close()
        fail(409, "CONFLICT", "邮箱已被注册")
    stamp = now_iso()
    cur = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (payload.name, payload.email, json.dumps(payload.skills, ensure_ascii=False), payload.max_concurrent_tasks, payload.status, hash_password(payload.password) if payload.password else None, stamp, stamp),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return public_user(row)


@router.get("/api/users")
def list_users(request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request)
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall(); conn.close()
    return {"items": [public_user(row) for row in rows]}


@router.get("/api/users/me/authorizations")
def get_my_authorizations(request: Request) -> dict[str, Any]:
    conn = db()
    user = require_user(conn, request)
    payload = get_authorization(conn, user["id"])
    conn.close()
    return payload


@router.patch("/api/users/me/authorizations")
def update_my_authorizations(payload: ProfileAuthorizationUpdate, request: Request) -> dict[str, Any]:
    conn = db()
    user = require_user(conn, request)
    compatibility_values = [
        value for value in (payload.cross_project_profile, payload.collaboration_analysis, payload.history_visible)
        if value is not None
    ]
    if compatibility_values and len(set(compatibility_values)) > 1:
        conn.close()
        fail(422, "VALIDATION_ERROR", "当前授权采用统一全局开关，三个兼容字段必须一致")
    global_enabled = payload.global_enabled
    if global_enabled is None and compatibility_values:
        global_enabled = compatibility_values[0]
    try:
        result = update_authorization(
            conn,
            user["id"],
            global_enabled=global_enabled,
            project_overrides=payload.project_overrides,
        )
    except ValueError as exc:
        conn.close()
        fail(400, "BAD_REQUEST", str(exc))
    conn.commit()
    conn.close()
    return result


@router.delete("/api/users/me/profile-data")
def delete_my_profile_data(request: Request) -> dict[str, Any]:
    conn = db()
    user = require_user(conn, request)
    result = delete_derived_profile_data(conn, user["id"])
    conn.commit()
    conn.close()
    return result


@router.get("/api/users/me/profile")
def get_my_profile(request: Request) -> dict[str, Any]:
    conn = db()
    current = require_user(conn, request)
    profile = build_profile_internal(conn, current["id"], self_view=True)
    conn.close()
    return profile_payload(profile)


@router.get("/api/users/me/collaborations")
def get_my_collaborations(request: Request) -> dict[str, Any]:
    conn = db()
    current = require_user(conn, request)
    payload = build_collaborations(conn, current["id"])
    conn.close()
    return payload


@router.get("/api/users/me/recommendations")
def get_my_long_term_recommendations(request: Request) -> dict[str, Any]:
    conn = db()
    current = require_user(conn, request)
    payload = build_long_term_recommendations(conn, current["id"])
    conn.close()
    return payload

@router.get("/api/users/{user_id}/profile")
def get_user_profile(user_id: int, request: Request) -> dict[str, Any]:
    conn = db()
    current = require_user(conn, request)
    target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        conn.close()
        fail(404, "NOT_FOUND", "用户不存在")
    if current["id"] == target["id"]:
        profile = build_profile_internal(conn, target["id"], self_view=True)
        conn.close()
        return profile_payload(profile)
    same_project = conn.execute(
        """SELECT a.project_id FROM memberships a
           JOIN memberships b ON a.project_id=b.project_id
           JOIN projects p ON p.id=a.project_id AND p.deleted_at IS NULL
           WHERE a.user_id=? AND b.user_id=? AND a.status='active' AND b.status='active'
           ORDER BY a.project_id LIMIT 1""",
        (current["id"], target["id"]),
    ).fetchone()
    if same_project is None:
        conn.close()
        fail(403, "FORBIDDEN", "无权查看该成员画像")
    profile = build_profile_internal(conn, target["id"], authorized_only=True)
    conn.close()
    return profile_payload(profile)

@router.get("/api/users/{user_id}")
def get_user(user_id: int, request: Request) -> dict[str, Any]:
    conn = db(); require_user(conn, request)
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(); conn.close()
    if not row: fail(404, "NOT_FOUND", "用户不存在")
    return public_user(row)

__all__ = ['register', 'login', 'logout', 'me', 'update_me', 'create_user', 'list_users', 'get_user', 'get_user_profile']
