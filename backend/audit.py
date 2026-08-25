from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def write_audit(
    conn: sqlite3.Connection,
    *,
    action: str,
    actor_id: Optional[int] = None,
    project_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str | int] = None,
    status_code: Optional[int] = None,
    before: Any = None,
    after: Any = None,
    request_method: Optional[str] = None,
    request_path: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    metadata: Any = None,
) -> None:
    """Write one immutable audit event. The table is created by the DB bootstrap/migration."""
    conn.execute(
        """INSERT INTO audit_logs(
            actor_id, project_id, action, resource_type, resource_id,
            status_code, before_data, after_data, request_method, request_path,
            ip_address, user_agent, metadata, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            actor_id,
            project_id,
            action,
            resource_type,
            str(resource_id) if resource_id is not None else None,
            status_code,
            _json(before),
            _json(after),
            request_method,
            request_path,
            ip_address,
            user_agent,
            _json(metadata),
            utc_now_iso(),
        ),
    )


def redact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove credential-like keys before storing request metadata."""
    sensitive = {"password", "password_hash", "token", "authorization", "cookie", "api_key", "llm_api_key"}
    return {key: "[REDACTED]" if key.lower() in sensitive else val for key, val in value.items()}


def token_fingerprint(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
