from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from backend.audit import write_audit
from backend.auth import SessionError, current_user
from backend.core.context import *
from backend.core.errors import APIError, error_payload
from backend.rate_limit import RateLimitMiddleware
from backend.routers import ALL_ROUTERS
from backend.routers.agent import *
from backend.routers.analytics import *
from backend.routers.auth_users import *
from backend.routers.contributions import *
from backend.routers.projects import *
from backend.routers.system import *
from backend.routers.tasks import *
from backend.schemas import *
from backend.services.agent_runtime import get_agent_runtime
from backend.services.analytics import *

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("COLLAB_DB", ROOT / "collab.db"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="协作账本 API", version="1.0.0", description="面向小组作业的贡献留痕与智能协作 API", lifespan=lifespan)
origins = [item.strip() for item in os.getenv("COLLAB_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"])
app.add_middleware(RateLimitMiddleware, trusted_proxy=os.getenv("COLLAB_TRUST_PROXY", "false").lower() == "true")
logger = logging.getLogger("collab_ledger")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _audit_resource(path: str) -> tuple[str, Optional[str]]:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1], parts[2] if len(parts) >= 3 and parts[2].isdigit() else None
    return "http", None


def _audit_project_id(conn, path: str) -> Optional[int]:
    parts = [part for part in path.strip("/").split("/") if part]
    try:
        if "projects" in parts:
            index = parts.index("projects")
            if index + 1 < len(parts) and parts[index + 1].isdigit():
                return int(parts[index + 1])
        for resource, table in (("tasks", "tasks"), ("contributions", "contributions"), ("invitations", "project_invitations")):
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == resource and parts[2].isdigit():
                row = conn.execute(f"SELECT project_id FROM {table} WHERE id=?", (int(parts[2]),)).fetchone()
                return row["project_id"] if row else None
    except (TypeError, ValueError, sqlite3.Error, SQLAlchemyError):
        return None
    return None


@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    should_audit = request.method in MUTATING_METHODS and request.url.path.startswith("/api/")
    actor_id = None
    if should_audit:
        identity_conn = None
        try:
            identity_conn = db()
            actor = current_user(identity_conn, request, required=False)
            actor_id = actor["id"] if actor else None
        except SessionError:
            actor_id = None
        finally:
            if identity_conn is not None:
                identity_conn.close()
    response = await call_next(request)
    if not should_audit:
        return response
    conn = None
    try:
        conn = db()
        resource_type, resource_id = _audit_resource(request.url.path)
        write_audit(conn, actor_id=actor_id, project_id=_audit_project_id(conn, request.url.path), action=f"{request.method} {request.url.path}", resource_type=resource_type, resource_id=resource_id, status_code=response.status_code, request_method=request.method, request_path=request.url.path, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"), metadata={"request_id": request.headers.get("x-request-id")})
        conn.commit()
    except Exception:
        logger.exception("failed to write audit event for %s %s", request.method, request.url.path)
        if conn is not None:
            conn.rollback()
    finally:
        if conn is not None:
            conn.close()
    return response


@app.exception_handler(APIError)
async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, exc.details))


@app.exception_handler(SessionError)
async def session_error_handler(_: Request, exc: SessionError) -> JSONResponse:
    return JSONResponse(status_code=401, content=error_payload("UNAUTHORIZED", str(exc)))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", ()) if part not in ("body", "query", "path")]
        details.append({"field": ".".join(loc) or "request", "message": "字段格式不正确"})
    return JSONResponse(status_code=422, content=error_payload("VALIDATION_ERROR", "请求参数不正确", details))


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled API error")
    return JSONResponse(status_code=500, content=error_payload("INTERNAL_ERROR", "服务器内部错误"))


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    codes = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 502: "LLM_PROVIDER_ERROR"}
    message = exc.detail if isinstance(exc.detail, str) else "请求失败"
    if exc.status_code == 404 and message == "Not Found":
        message = "资源不存在"
    elif exc.status_code == 405 and message == "Method Not Allowed":
        message = "请求方法不支持"
    return JSONResponse(status_code=exc.status_code, content=error_payload(codes.get(exc.status_code, "BAD_REQUEST"), message))


for router in ALL_ROUTERS:
    app.include_router(router)

FRONTEND_DIR = ROOT / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
