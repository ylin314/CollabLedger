from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Base

SCHEMA_VERSION = 4

SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT,
 skills TEXT NOT NULL DEFAULT '[]', max_concurrent_tasks INTEGER NOT NULL DEFAULT 3,
 status TEXT NOT NULL DEFAULT 'offline', password_hash TEXT, created_at TEXT NOT NULL,
 updated_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, project_type TEXT,
 description TEXT, start_date TEXT, end_date TEXT, owner_id INTEGER,
 status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT,
 archived_at TEXT, deleted_at TEXT, FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS memberships (
 project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'member',
 joined_at TEXT NOT NULL, updated_at TEXT, PRIMARY KEY(project_id,user_id),
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, title TEXT NOT NULL,
 description TEXT, assignee_id INTEGER, status TEXT NOT NULL DEFAULT 'unassigned',
 due_date TEXT, estimated_hours REAL, actual_hours REAL, quality REAL, task_type TEXT,
 priority TEXT NOT NULL DEFAULT 'medium', created_by INTEGER, reviewer_id INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(assignee_id) REFERENCES users(id) ON DELETE SET NULL,
 FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
 FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS task_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, user_id INTEGER,
 action TEXT NOT NULL, from_status TEXT, to_status TEXT, note TEXT, at TEXT NOT NULL,
 FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS contributions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 kind TEXT NOT NULL, title TEXT, description TEXT, quantity REAL NOT NULL DEFAULT 1,
 metadata TEXT NOT NULL DEFAULT '{}', evidence_url TEXT, status TEXT NOT NULL DEFAULT 'pending',
 source TEXT NOT NULL DEFAULT 'manual', occurred_at TEXT, created_at TEXT NOT NULL,
 updated_at TEXT, created_by INTEGER, confirmed_by INTEGER, confirmed_at TEXT,
 confirmation_note TEXT, dispute_note TEXT, deleted_at TEXT,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_invitations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, inviter_id INTEGER NOT NULL,
 invite_hash TEXT NOT NULL UNIQUE, invite_code TEXT NOT NULL UNIQUE, email TEXT,
 role TEXT NOT NULL DEFAULT 'member', expires_at TEXT NOT NULL, accepted_at TEXT,
 created_at TEXT NOT NULL, max_uses INTEGER NOT NULL DEFAULT 1, used_count INTEGER NOT NULL DEFAULT 0,
 revoked INTEGER NOT NULL DEFAULT 0, revoked_at TEXT, updated_at TEXT, is_mentor INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(inviter_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS work_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 work_date TEXT NOT NULL, hours REAL NOT NULL DEFAULT 0, note TEXT, check_in TEXT,
 check_out TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, UNIQUE(project_id,user_id,work_date)
);
CREATE TABLE IF NOT EXISTS quality_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, task_id INTEGER,
 reviewer_id INTEGER NOT NULL, reviewee_id INTEGER NOT NULL, score REAL NOT NULL,
 comment TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
 FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(reviewee_id) REFERENCES users(id) ON DELETE CASCADE,
 UNIQUE(project_id,task_id,reviewer_id,reviewee_id)
);
CREATE TABLE IF NOT EXISTS task_checkins (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, content TEXT NOT NULL, hours REAL NOT NULL, blockers TEXT,
 created_at TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS task_reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL UNIQUE, reviewer_id INTEGER NOT NULL,
 quality REAL NOT NULL, comment TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
 FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS task_review_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, reviewer_id INTEGER NOT NULL,
 quality REAL NOT NULL, comment TEXT, created_at TEXT NOT NULL, updated_at TEXT,
 FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
 FOREIGN KEY(reviewer_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS agent_memory (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
 session_id TEXT NOT NULL DEFAULT 'default', role TEXT NOT NULL, content TEXT NOT NULL,
 created_at TEXT NOT NULL, user_id INTEGER
);
CREATE TABLE IF NOT EXISTS platform_connections (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, platform TEXT NOT NULL,
 external_account_id TEXT, credentials_ref TEXT, status TEXT NOT NULL DEFAULT 'active',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_integrations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, connection_id INTEGER NOT NULL,
 platform TEXT NOT NULL, config TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(connection_id) REFERENCES platform_connections(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS external_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, integration_id INTEGER NOT NULL, external_id TEXT NOT NULL,
 event_type TEXT, payload TEXT NOT NULL DEFAULT '{}', occurred_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(integration_id, external_id), FOREIGN KEY(integration_id) REFERENCES project_integrations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sync_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, integration_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 cursor TEXT, error TEXT, started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL,
 FOREIGN KEY(integration_id) REFERENCES project_integrations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS agent_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER,
 session_key TEXT NOT NULL, title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(project_id, session_key), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS agent_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, role TEXT NOT NULL,
 content TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recommendations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, task_id INTEGER,
 task_name TEXT, generated_by INTEGER, payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 mode TEXT NOT NULL DEFAULT 'single', status TEXT NOT NULL DEFAULT 'generated', source TEXT NOT NULL DEFAULT 'rule',
 accepted_user_id INTEGER, accepted_at TEXT, assigned_user_id INTEGER, assigned_at TEXT,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
 FOREIGN KEY(generated_by) REFERENCES users(id) ON DELETE SET NULL,
 FOREIGN KEY(accepted_user_id) REFERENCES users(id) ON DELETE SET NULL,
 FOREIGN KEY(assigned_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS recommendation_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, recommendation_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
 task_id INTEGER, actor_id INTEGER, action TEXT NOT NULL, selected_user_id INTEGER, note TEXT,
 payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 FOREIGN KEY(recommendation_id) REFERENCES recommendations(id) ON DELETE CASCADE,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
 FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
 FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE SET NULL,
 FOREIGN KEY(selected_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, project_id INTEGER, action TEXT NOT NULL,
 resource_type TEXT, resource_id TEXT, status_code INTEGER, before_data TEXT, after_data TEXT,
 request_method TEXT, request_path TEXT, ip_address TEXT, user_agent TEXT, metadata TEXT,
 created_at TEXT NOT NULL, FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE SET NULL,
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
);
'''


_ENGINES: dict[str, Engine] = {}
_INSERT_ID_TABLES = {
    "users", "projects", "tasks", "task_logs", "contributions", "auth_sessions",
    "project_invitations", "work_logs", "quality_reviews", "task_checkins", "task_reviews",
    "task_review_history", "agent_memory", "platform_connections", "project_integrations",
    "external_events", "sync_jobs", "agent_sessions", "agent_messages", "recommendations", "recommendation_events", "audit_logs",
}


def database_url(path: str | Path | None = None) -> str | URL:
    configured = os.getenv("COLLAB_DATABASE_URL") or os.getenv("DATABASE_URL")
    if configured:
        return configured
    postgres_host = os.getenv("POSTGRES_HOST")
    if postgres_host:
        return URL.create(
            "postgresql+psycopg",
            username=os.getenv("POSTGRES_USER", "collab"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            host=postgres_host,
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "collab_ledger"),
        )
    resolved = Path(path or os.getenv("COLLAB_DB", "collab.db")).expanduser().resolve()
    return f"sqlite:///{resolved.as_posix()}"


def _url_text(url: str | URL) -> str:
    return url.render_as_string(hide_password=False) if isinstance(url, URL) else url


def _is_sqlite(url: str | URL) -> bool:
    return url.drivername.startswith("sqlite") if isinstance(url, URL) else url.startswith("sqlite:")


def get_engine(path: str | Path | None = None) -> Engine:
    url = database_url(path)
    key = _url_text(url)
    engine = _ENGINES.get(key)
    if engine is None:
        options: dict[str, Any] = {"pool_pre_ping": True}
        if _is_sqlite(url):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        engine = create_engine(url, **options)
        _ENGINES[key] = engine
    return engine


@contextmanager
def session_scope(path: str | Path | None = None) -> Iterator[Session]:
    session = sessionmaker(bind=get_engine(path), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class CompatRow(Mapping[str, Any]):
    def __init__(self, data: Mapping[str, Any]):
        self._data = dict(data)
        self._values = list(self._data.values())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):
        return self._data.keys()


class CompatCursor:
    def __init__(self, rows: list[CompatRow], *, rowcount: int = -1, lastrowid: Optional[int] = None):
        self._rows = rows
        self._index = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> Optional[CompatRow]:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[CompatRow]:
        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows


class SQLAlchemyCompatConnection:
    """DB-API-shaped adapter allowing legacy qmark SQL to run on PostgreSQL.

    New repositories should use ``session_scope`` directly. This adapter keeps the
    established API stable while the query layer is migrated incrementally.
    """

    def __init__(self, connection: Connection):
        self._connection = connection

    @staticmethod
    def _prepare(sql: str, parameters: Any) -> tuple[str, dict[str, Any]]:
        statement = re.sub(
            r"([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\?\s+COLLATE\s+NOCASE",
            r"LOWER(\1)=LOWER(?)",
            sql,
            flags=re.IGNORECASE,
        )
        if "sqlite_master" in statement:
            statement = statement.replace(
                "sqlite_master WHERE type='table' AND name=",
                "information_schema.tables WHERE table_schema=current_schema() AND table_name=",
            )
        values = list(parameters or ()) if not isinstance(parameters, dict) else []
        if isinstance(parameters, dict):
            return statement, dict(parameters)
        bindings: dict[str, Any] = {}
        chunks = statement.split("?")
        if len(chunks) - 1 != len(values):
            raise ValueError(f"SQL placeholder mismatch: expected {len(chunks)-1}, got {len(values)}")
        output = [chunks[0]]
        for index, value in enumerate(values):
            name = f"p{index}"
            bindings[name] = value
            output.extend((f":{name}", chunks[index + 1]))
        return "".join(output), bindings

    def execute(self, sql: str, parameters: Any = ()) -> CompatCursor:
        statement, bindings = self._prepare(sql, parameters)
        match = re.match(r"\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", statement, re.IGNORECASE)
        wants_id = bool(match and match.group(1).lower() in _INSERT_ID_TABLES and " returning " not in statement.lower())
        if wants_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"
        result = self._connection.execute(text(statement), bindings)
        lastrowid = None
        rows: list[CompatRow] = []
        if result.returns_rows:
            mappings = result.mappings().all()
            rows = [CompatRow(row) for row in mappings]
            if wants_id and rows:
                lastrowid = int(rows[0]["id"])
                rows = []
        return CompatCursor(rows, rowcount=result.rowcount, lastrowid=lastrowid)

    def executescript(self, script: str) -> None:
        for statement in (part.strip() for part in script.split(";")):
            if statement:
                self.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect(path: str | Path | None = None):
    url = database_url(path)
    if _is_sqlite(url):
        url_text = _url_text(url)
        resolved = Path(url_text.removeprefix("sqlite:///")).resolve()
        conn = sqlite3.connect(resolved, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn
    return SQLAlchemyCompatConnection(get_engine(path).connect())


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_columns(conn: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = _columns(conn, table)
    for name, definition in definitions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def initialize(path: str | Path | None = None) -> None:
    url = database_url(path)
    if not _is_sqlite(url):
        Base.metadata.create_all(get_engine(path))
        return
    conn = connect(path)
    legacy_contributions = "contributions" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contributions'")] and "status" not in _columns(conn, "contributions")
    conn.executescript(SCHEMA_SQL)
    # Forward-compatible upgrades for databases created by pre-Alembic versions.
    _add_columns(conn, "users", {"password_hash": "TEXT", "updated_at": "TEXT"})
    _add_columns(conn, "projects", {"status": "TEXT NOT NULL DEFAULT 'active'", "updated_at": "TEXT", "archived_at": "TEXT", "deleted_at": "TEXT"})
    _add_columns(conn, "memberships", {"updated_at": "TEXT"})
    _add_columns(conn, "tasks", {"priority": "TEXT NOT NULL DEFAULT 'medium'", "created_by": "INTEGER", "reviewer_id": "INTEGER", "deleted_at": "TEXT"})
    _add_columns(conn, "task_logs", {"from_status": "TEXT", "to_status": "TEXT"})
    _add_columns(conn, "project_invitations", {"max_uses": "INTEGER NOT NULL DEFAULT 1", "used_count": "INTEGER NOT NULL DEFAULT 0", "revoked": "INTEGER NOT NULL DEFAULT 0", "revoked_at": "TEXT", "updated_at": "TEXT", "is_mentor": "INTEGER NOT NULL DEFAULT 0"})
    _add_columns(conn, "contributions", {"evidence_url": "TEXT", "status": "TEXT NOT NULL DEFAULT 'pending'", "source": "TEXT NOT NULL DEFAULT 'manual'", "occurred_at": "TEXT", "updated_at": "TEXT", "created_by": "INTEGER", "confirmed_by": "INTEGER", "confirmed_at": "TEXT", "confirmation_note": "TEXT", "dispute_note": "TEXT", "deleted_at": "TEXT"})
    _add_columns(conn, "agent_memory", {"user_id": "INTEGER"})
    _add_columns(conn, "recommendations", {
        "mode": "TEXT NOT NULL DEFAULT 'single'",
        "status": "TEXT NOT NULL DEFAULT 'generated'",
        "source": "TEXT NOT NULL DEFAULT 'rule'",
        "accepted_user_id": "INTEGER",
        "accepted_at": "TEXT",
        "assigned_user_id": "INTEGER",
        "assigned_at": "TEXT",
    })
    _add_columns(conn, "task_review_history", {"updated_at": "TEXT"})
    if "updated_at" in _columns(conn, "task_review_history"):
        conn.execute("UPDATE task_review_history SET updated_at=COALESCE(updated_at,created_at)")
    stamp = now_iso()
    for table, created, updated in (("users", "created_at", "updated_at"), ("projects", "created_at", "updated_at"), ("memberships", "joined_at", "updated_at"), ("contributions", "created_at", "updated_at")):
        if updated in _columns(conn, table):
            conn.execute(f"UPDATE {table} SET {updated}=COALESCE({updated},{created},?)", (stamp,))
    if "occurred_at" in _columns(conn, "contributions"):
        conn.execute("UPDATE contributions SET occurred_at=COALESCE(occurred_at,created_at,?), created_by=COALESCE(created_by,user_id)", (stamp,))
    if legacy_contributions:
        conn.execute("UPDATE contributions SET status='confirmed', confirmed_at=COALESCE(confirmed_at,created_at,?), confirmed_by=COALESCE(confirmed_by,(SELECT owner_id FROM projects WHERE projects.id=contributions.project_id))", (stamp,))
    conn.execute("UPDATE tasks SET priority=COALESCE(priority,'medium'), created_by=COALESCE(created_by,assignee_id)")
    conn.execute("UPDATE project_invitations SET used_count=CASE WHEN accepted_at IS NOT NULL AND used_count=0 THEN 1 ELSE used_count END")
    timestamp_columns = {
        "users": ("created_at", "updated_at"), "projects": ("created_at", "updated_at", "archived_at", "deleted_at"),
        "memberships": ("joined_at", "updated_at"), "tasks": ("created_at", "updated_at", "deleted_at"),
        "task_logs": ("at",), "contributions": ("occurred_at", "created_at", "updated_at", "confirmed_at", "deleted_at"),
        "project_invitations": ("expires_at", "accepted_at", "created_at", "updated_at", "revoked_at"),
        "auth_sessions": ("created_at", "expires_at", "revoked_at"), "work_logs": ("check_in", "check_out", "created_at", "updated_at"),
        "quality_reviews": ("created_at", "updated_at"), "audit_logs": ("created_at",),
        "task_review_history": ("created_at", "updated_at"),
    }
    for table, columns in timestamp_columns.items():
        existing = _columns(conn, table)
        for column in columns:
            if column in existing:
                conn.execute(f"UPDATE {table} SET {column}=replace({column}, '+00:00', 'Z') WHERE {column} LIKE '%+00:00'")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id,project_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id,deleted_at,status);
        CREATE INDEX IF NOT EXISTS idx_checkins_project ON task_checkins(project_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_contributions_project ON contributions(project_id,deleted_at,status);
        CREATE INDEX IF NOT EXISTS idx_audit_project_time ON audit_logs(project_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_memory_project ON agent_memory(project_id,session_id,id);
        CREATE INDEX IF NOT EXISTS idx_recommendations_project ON recommendations(project_id,task_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_recommendation_events_project ON recommendation_events(project_id,created_at);
    """)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
    conn.close()


def schema_status(path: str | Path | None = None) -> dict[str, object]:
    url = database_url(path)
    if not _is_sqlite(url):
        tables = sorted(inspect(get_engine(path)).get_table_names())
        return {"version": "alembic", "expected_version": "head", "tables": tables, "dialect": get_engine(path).dialect.name}
    conn = connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    conn.close()
    return {"version": version, "expected_version": SCHEMA_VERSION, "tables": tables, "dialect": "sqlite"}
