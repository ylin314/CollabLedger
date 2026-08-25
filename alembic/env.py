from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL

from backend.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# DATABASE_URL enables PostgreSQL; COLLAB_DB keeps the zero-config SQLite path.
configured_url = os.getenv("COLLAB_DATABASE_URL") or os.getenv("DATABASE_URL")
if configured_url:
    runtime_url = configured_url
elif os.getenv("POSTGRES_HOST"):
    runtime_url = URL.create(
        "postgresql+psycopg", username=os.getenv("POSTGRES_USER", "collab"),
        password=os.getenv("POSTGRES_PASSWORD", ""), host=os.getenv("POSTGRES_HOST"),
        port=int(os.getenv("POSTGRES_PORT", "5432")), database=os.getenv("POSTGRES_DB", "collab_ledger"),
    ).render_as_string(hide_password=False)
else:
    db_path = Path(os.getenv("COLLAB_DB", Path(__file__).resolve().parents[1] / "collab.db"))
    runtime_url = f"sqlite:///{db_path.as_posix()}"
config.set_main_option("sqlalchemy.url", runtime_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
