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
    assert len(Base.metadata.tables) == 22
    assert {"audit_logs", "platform_connections", "agent_sessions", "recommendations"} <= set(Base.metadata.tables)
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
    assert status["dialect"] == "sqlite" and len(status["tables"]) == 22


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
                "tasks": {"id"},
                "project_invitations": {"id"},
                "task_review_history": {"id", "created_at"},
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
