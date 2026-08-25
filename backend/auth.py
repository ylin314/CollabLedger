"""HTTP-only Cookie Session 与密码散列工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request

PASSWORD_ITERATIONS = 240_000
SESSION_DAYS = 7
COOKIE_NAME = "collab_session"


class SessionError(Exception):
    """登录态缺失或失效。"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: Optional[datetime] = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("密码至少需要 8 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
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
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(40)
    created = utc_now()
    expires = created + timedelta(days=SESSION_DAYS)
    conn.execute(
        "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
        (token_digest(token), user_id, iso_utc(created), iso_utc(expires)),
    )
    return token, iso_utc(expires)


def request_token(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    # 兼容旧客户端；契约客户端应使用 Cookie。
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if value and scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def current_user(conn: sqlite3.Connection, request: Optional[Request], required: bool = False) -> Optional[sqlite3.Row]:
    token = request_token(request)
    if not token:
        if required:
            raise SessionError("请先登录")
        return None
    row = conn.execute(
        """SELECT u.*, s.token_hash, s.expires_at session_expires_at
           FROM auth_sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.revoked_at IS NULL""",
        (token_digest(token),),
    ).fetchone()
    if not row:
        raise SessionError("登录已失效，请重新登录")
    try:
        expired = parse_utc(row["session_expires_at"]) <= utc_now()
    except (TypeError, ValueError):
        expired = True
    if expired:
        conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?", (iso_utc(), token_digest(token)))
        conn.commit()
        raise SessionError("登录已失效，请重新登录")
    return row


def revoke_session(conn: sqlite3.Connection, request: Optional[Request]) -> bool:
    token = request_token(request)
    if not token:
        return False
    cur = conn.execute(
        "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
        (iso_utc(), token_digest(token)),
    )
    return cur.rowcount > 0
