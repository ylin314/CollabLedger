"""轻量级会话认证工具。

项目当前使用 SQLite，因此不额外引入 JWT 依赖：登录后生成随机 opaque token，数据库只保存
token 的 SHA-256 摘要。这样支持服务端撤销会话（退出登录），也避免把用户信息放在可伪造的
客户端 payload 中。密码使用 PBKDF2-HMAC-SHA256 加盐存储。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlite3
from fastapi import HTTPException, Request


PASSWORD_ITERATIONS = 240_000
SESSION_DAYS = 7


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("密码至少需要 8 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not encoded or not encoded.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expected = base64.urlsafe_b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(40)
    created = utc_now()
    expires = created + timedelta(days=SESSION_DAYS)
    conn.execute(
        "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
        (token_digest(token), user_id, created.isoformat(), expires.isoformat()),
    )
    return token, expires.isoformat()


def bearer_token(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    value = request.headers.get("authorization", "")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Authorization 需要 Bearer token")
    return token.strip()


def current_user(conn: sqlite3.Connection, request: Optional[Request], required: bool = False) -> Optional[sqlite3.Row]:
    token = bearer_token(request)
    if not token:
        if required:
            raise HTTPException(status_code=401, detail="请先登录")
        return None
    row = conn.execute(
        """SELECT u.*, s.token_hash FROM auth_sessions s
           JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?""",
        (token_digest(token), iso_now()),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return row


def revoke_session(conn: sqlite3.Connection, request: Optional[Request]) -> bool:
    token = bearer_token(request)
    if not token:
        return False
    cur = conn.execute(
        "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
        (iso_now(), token_digest(token)),
    )
    return cur.rowcount > 0
