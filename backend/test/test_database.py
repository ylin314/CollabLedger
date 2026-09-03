from __future__ import annotations

from sqlalchemy import create_engine, create_mock_engine, select

from backend import db
from backend.models import Base, User


def test_complete_sqlalchemy_metadata_compiles_for_postgresql():
    statements = []
    engine = create_mock_engine(
        "postgresql+psycopg://user:pass@localhost/database",
        lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))),
    )
    Base.metadata.create_all(engine)
    assert len(Base.metadata.tables) == 30
    assert {"audit_logs", "platform_connections", "agent_sessions", "recommendations", "recommendation_events", "weekly_reports"} <= set(Base.metadata.tables)
    assert statements


def test_sqlalchemy_session_and_schema_status_on_sqlite(tmp_path, monkeypatch):
    database = tmp_path / "sqlalchemy.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("COLLAB_DATABASE_URL", raising=False)
    db.initialize(database)
    with db.session_scope(database) as session:
        session.add(User(name="SQLAlchemy", email="sa@example.com", created_at="2026-08-25T00:00:00Z"))
    with db.session_scope(database) as session:
        assert session.scalar(select(User.name).where(User.email == "sa@example.com")) == "SQLAlchemy"
    status = db.schema_status(database)
    assert status["dialect"] == "sqlite" and len(status["tables"]) == 30


def test_postgresql_compat_sql_translation():
    statement, bindings = db.SQLAlchemyCompatConnection._prepare(
        "SELECT * FROM users WHERE lower(email)=lower(?) AND id=?",
        ("a@example.com", 7),
    )
    assert statement.endswith("lower(:p0) AND id=:p1")
    assert bindings == {"p0": "a@example.com", "p1": 7}


def test_postgresql_initialize_adds_new_columns_to_existing_schema(monkeypatch):
    class FakeInspector:
        def get_columns(self, table):
            return [{"name": name} for name in {
            "users": {"avatar_url"},
                "tasks": {"id"},
                "project_invitations": {"id"},
                "task_review_history": {"id", "created_at"},
                "platform_connections": {"id", "external_account_id", "credentials_ref", "status", "created_at", "updated_at"},
                "agent_memory": {"id", "project_id", "session_id", "role", "content", "created_at"},
                "oauth_states": {"state", "user_id", "platform", "redirect_uri", "expires_at", "created_at", "consumed_at"},
            }[table]]

    executed = []

    class FakeConnection:
        def execute(self, statement):
            executed.append(str(statement))

    class FakeEngine:
        def begin(self):
            class Context:
                def __enter__(self):
                    return FakeConnection()

                def __exit__(self, exc_type, exc, traceback):
                    return False

            return Context()

    monkeypatch.setattr(db, "get_engine", lambda path=None: FakeEngine())
    monkeypatch.setattr(db, "inspect", lambda connection: FakeInspector())
    db._initialize_postgresql()
    assert any('ALTER TABLE "tasks" ADD COLUMN "reviewer_id" INTEGER' in statement for statement in executed)
    assert any('ALTER TABLE "project_invitations" ADD COLUMN "is_mentor" INTEGER NOT NULL DEFAULT 0' in statement for statement in executed)
    assert any('ALTER TABLE "task_review_history" ADD COLUMN "updated_at" VARCHAR(40)' in statement for statement in executed)
    assert any('ALTER TABLE "platform_connections" ADD COLUMN "external_username" VARCHAR(255)' in statement for statement in executed)
    assert any('ALTER TABLE "oauth_states" ADD COLUMN "session_hash" VARCHAR(64)' in statement for statement in executed)


def test_compat_connection_insert_select_and_lastrowid(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'compat.db').as_posix()}")
    Base.metadata.create_all(engine)
    conn = db.SQLAlchemyCompatConnection(engine.connect())
    created = conn.execute(
        "INSERT INTO users(name,email,skills,max_concurrent_tasks,status,created_at) VALUES (?,?,?,?,?,?)",
        ("Compat", "compat@example.com", "[]", 3, "offline", "2026-08-25T00:00:00Z"),
    )
    assert created.lastrowid == 1
    row = conn.execute("SELECT id,name FROM users WHERE id=?", (created.lastrowid,)).fetchone()
    assert row[0] == 1 and row["name"] == "Compat" and dict(row)["id"] == 1
    conn.commit(); conn.close()


def test_alembic_upgrade_is_repeatable_and_preserves_legacy(tmp_path, monkeypatch):
    import sqlite3
    from alembic import command
    from alembic.config import Config

    database = tmp_path / "legacy-alembic.db"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT,skills TEXT NOT NULL DEFAULT '[]',max_concurrent_tasks INTEGER NOT NULL DEFAULT 3,status TEXT NOT NULL DEFAULT 'offline',created_at TEXT NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,project_type TEXT,description TEXT,start_date TEXT,end_date TEXT,owner_id INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE contributions (id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,user_id INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT,description TEXT,quantity REAL NOT NULL DEFAULT 1,metadata TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
        INSERT INTO users(name,email,created_at) VALUES ('legacy','legacy@example.com','2026-08-20T10:00:00+00:00');
        INSERT INTO projects(name,owner_id,created_at) VALUES ('legacy project',1,'2026-08-20T10:00:00+00:00');
        INSERT INTO contributions(project_id,user_id,kind,title,created_at) VALUES (1,1,'code','legacy contribution','2026-08-20T12:00:00+00:00');
        """
    )
    conn.commit(); conn.close()
    monkeypatch.setenv("COLLAB_DB", str(database))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("COLLAB_DATABASE_URL", raising=False)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    conn = sqlite3.connect(database)
    assert conn.execute("SELECT name,status FROM projects WHERE id=1").fetchone() == ("legacy project", "active")
    assert conn.execute("SELECT title,status FROM contributions WHERE id=1").fetchone() == ("legacy contribution", "confirmed")
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_initial"
    conn.close()


def test_postgresql_url_escapes_special_password(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("COLLAB_DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_USER", "collab")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:/ word")
    monkeypatch.setenv("POSTGRES_DB", "collab_ledger")
    url = db.database_url()
    rendered = db._url_text(url)
    assert "p%40ss%3A%2F word" in rendered.replace("%20", " ")
    assert rendered.startswith("postgresql+psycopg://collab:")


def test_sqlite_agent_session_scope_migration_preserves_message_foreign_keys(tmp_path):
    import sqlite3

    database = tmp_path / "agent-session-legacy.db"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, skills TEXT NOT NULL DEFAULT '[]', max_concurrent_tasks INTEGER NOT NULL DEFAULT 3, status TEXT NOT NULL DEFAULT 'offline', created_at TEXT NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER,
            session_key TEXT NOT NULL, title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(project_id, session_key), FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, user_id INTEGER
        );
        INSERT INTO users(id,name,email,created_at) VALUES (1,'用户一','one@example.com','2026-09-01T00:00:00Z'),(2,'用户二','two@example.com','2026-09-01T00:00:00Z');
        INSERT INTO projects(id,name,owner_id,created_at) VALUES (1,'旧项目',1,'2026-09-01T00:00:00Z');
        INSERT INTO agent_sessions(id,project_id,user_id,session_key,title,created_at,updated_at) VALUES (7,1,NULL,'shared','旧标题','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z');
        INSERT INTO agent_messages(id,session_id,role,content,created_at) VALUES (9,7,'user','旧消息','2026-09-01T00:00:00Z');
        INSERT INTO agent_memory(project_id,session_id,role,content,created_at,user_id) VALUES
            (1,'shared','user','用户一消息','2026-09-01T00:00:00Z',1),
            (1,'shared','user','用户二消息','2026-09-01T00:00:01Z',2);
        """
    )
    conn.commit()
    conn.close()

    db.initialize(database)
    db.initialize(database)

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    unique_indexes = []
    for index in conn.execute("PRAGMA index_list(agent_sessions)").fetchall():
        if index[2]:
            columns = tuple(row[2] for row in conn.execute(f"PRAGMA index_info({index[1]})").fetchall())
            unique_indexes.append(columns)
    assert ("project_id", "user_id", "session_key") in unique_indexes
    assert tuple(conn.execute("SELECT session_id,content FROM agent_messages WHERE id=9").fetchone()) == (7, "旧消息")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    scoped = conn.execute(
        "SELECT user_id,title FROM agent_sessions WHERE project_id=1 AND session_key='shared' ORDER BY user_id"
    ).fetchall()
    assert [(row["user_id"], row["title"]) for row in scoped] == [(None, "旧标题"), (1, "旧标题"), (2, "旧标题")]
    conn.execute(
        "INSERT INTO agent_sessions(project_id,user_id,session_key,title,created_at,updated_at) VALUES (1,1,'new','新','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z')"
    )
    try:
        conn.execute(
            "INSERT INTO agent_sessions(project_id,user_id,session_key,title,created_at,updated_at) VALUES (1,1,'new','重复','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z')"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("用户范围内的 Agent session 唯一约束未生效")
    conn.close()


def test_sqlite_agent_session_migration_adds_missing_memory_user_column(tmp_path):
    import sqlite3

    database = tmp_path / "agent-memory-legacy.db"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, skills TEXT NOT NULL DEFAULT '[]', max_concurrent_tasks INTEGER NOT NULL DEFAULT 3, status TEXT NOT NULL DEFAULT 'offline', created_at TEXT NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE agent_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE agent_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, user_id INTEGER, session_key TEXT NOT NULL, title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,session_key));
        INSERT INTO users(id,name,email,created_at) VALUES (1,'旧用户','legacy@example.com','2026-09-01T00:00:00Z');
        INSERT INTO projects(id,name,owner_id,created_at) VALUES (1,'旧项目',1,'2026-09-01T00:00:00Z');
        INSERT INTO agent_memory(project_id,session_id,role,content,created_at) VALUES (1,'legacy','user','旧消息','2026-09-01T00:00:00Z');
        INSERT INTO agent_sessions(project_id,user_id,session_key,title,created_at,updated_at) VALUES (1,NULL,'legacy','旧标题','2026-09-01T00:00:00Z','2026-09-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    db.initialize(database)

    conn = sqlite3.connect(database)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_memory)").fetchall()}
    assert "user_id" in columns
    assert conn.execute("SELECT content,user_id FROM agent_memory").fetchone() == ("旧消息", None)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
