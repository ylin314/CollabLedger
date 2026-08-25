from __future__ import annotations

from typing import Any, Optional


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[list[dict[str, Any]]] = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def fail(status_code: int, code: str, message: str, details: Optional[list[dict[str, Any]]] = None) -> None:
    raise APIError(status_code, code, message, details)


def error_payload(code: str, message: str, details: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}
