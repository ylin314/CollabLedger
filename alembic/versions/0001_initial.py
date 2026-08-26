from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from alembic import context, op

from backend.db import initialize
from backend.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _sqlite_path() -> Path:
    url = context.config.get_main_option("sqlalchemy.url")
    parsed = urlparse(url)
    if parsed.scheme != "sqlite":
        raise RuntimeError("0001_initial currently supports SQLite URLs only")
    raw = unquote(parsed.path)
    if raw.startswith("/") and len(raw) >= 3 and raw[2] == ":":  # Windows /C:/path
        raw = raw[1:]
    return Path(raw)


def upgrade() -> None:
    # SQLite needs the legacy-compatible upgrader; PostgreSQL is created from the
    # complete SQLAlchemy metadata.
    url = context.config.get_main_option("sqlalchemy.url")
    if url.startswith("sqlite:"):
        initialize(_sqlite_path())
    else:
        Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    url = context.config.get_main_option("sqlalchemy.url")
    if not url.startswith("sqlite:"):
        Base.metadata.drop_all(bind=bind)
        return
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    for table in (
        "audit_logs", "recommendation_events", "recommendations", "agent_messages", "agent_sessions", "sync_jobs", "external_events",
        "project_integrations", "platform_connections", "agent_memory", "task_review_history", "task_reviews",
        "task_checkins", "quality_reviews", "work_logs", "project_invitations", "auth_sessions", "contributions", "weekly_reports",
        "task_logs", "tasks", "memberships", "projects", "users",
    ):
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")
