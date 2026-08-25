from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Complete SQLAlchemy metadata for SQLite and PostgreSQL."""


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    skills: Mapped[str] = mapped_column(Text, server_default=text("'[]'"), nullable=False)
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, server_default=text("3"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'offline'"), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String(40))


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    project_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[str | None] = mapped_column(String(10))
    end_date: Mapped[str | None] = mapped_column(String(10))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'active'"), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String(40))
    archived_at: Mapped[str | None] = mapped_column(String(40))
    deleted_at: Mapped[str | None] = mapped_column(String(40))


class Membership(Base):
    __tablename__ = "memberships"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), server_default=text("'member'"), nullable=False)
    joined_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String(40))


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), server_default=text("'unassigned'"), nullable=False)
    due_date: Mapped[str | None] = mapped_column(String(10))
    estimated_hours: Mapped[float | None] = mapped_column(Float)
    actual_hours: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[float | None] = mapped_column(Float)
    task_type: Mapped[str | None] = mapped_column(String(100))
    priority: Mapped[str] = mapped_column(String(20), server_default=text("'medium'"), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(String(40))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(300), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    status_code: Mapped[int | None] = mapped_column(Integer)
    before_data: Mapped[str | None] = mapped_column(Text)
    after_data: Mapped[str | None] = mapped_column(Text)
    request_method: Mapped[str | None] = mapped_column(String(10))
    request_path: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(100))
    user_agent: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


metadata = Base.metadata


def table(name: str, *columns: Column, **kwargs) -> Table:
    return Table(name, metadata, *columns, **kwargs)


task_logs = table("task_logs",
    Column("id", Integer, primary_key=True), Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="SET NULL")), Column("action", String(100), nullable=False),
    Column("from_status", String(30)), Column("to_status", String(30)), Column("note", Text), Column("at", String(40), nullable=False))

contributions = table("contributions",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("kind", String(30), nullable=False),
    Column("title", String(200)), Column("description", Text), Column("quantity", Float, nullable=False, server_default=text("1")),
    Column("metadata", Text, nullable=False, server_default=text("'{}'")), Column("evidence_url", Text),
    Column("status", String(30), nullable=False, server_default=text("'pending'")), Column("source", String(50), nullable=False, server_default=text("'manual'")),
    Column("occurred_at", String(40)), Column("created_at", String(40), nullable=False), Column("updated_at", String(40)),
    Column("created_by", ForeignKey("users.id", ondelete="SET NULL")), Column("confirmed_by", ForeignKey("users.id", ondelete="SET NULL")),
    Column("confirmed_at", String(40)), Column("confirmation_note", Text), Column("dispute_note", Text), Column("deleted_at", String(40)))

auth_sessions = table("auth_sessions",
    Column("id", Integer, primary_key=True), Column("token_hash", String(64), nullable=False, unique=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("created_at", String(40), nullable=False),
    Column("expires_at", String(40), nullable=False), Column("revoked_at", String(40)))

project_invitations = table("project_invitations",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("inviter_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("invite_hash", String(128), nullable=False, unique=True),
    Column("invite_code", String(64), nullable=False, unique=True), Column("email", String(254)),
    Column("role", String(20), nullable=False, server_default=text("'member'")), Column("expires_at", String(40), nullable=False),
    Column("accepted_at", String(40)), Column("created_at", String(40), nullable=False), Column("max_uses", Integer, nullable=False, server_default=text("1")),
    Column("used_count", Integer, nullable=False, server_default=text("0")), Column("revoked", Integer, nullable=False, server_default=text("0")),
    Column("revoked_at", String(40)), Column("updated_at", String(40)), Column("is_mentor", Integer, nullable=False, server_default=text("0")))

work_logs = table("work_logs",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("work_date", String(10), nullable=False),
    Column("hours", Float, nullable=False, server_default=text("0")), Column("note", Text), Column("check_in", String(40)),
    Column("check_out", String(40)), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False),
    UniqueConstraint("project_id", "user_id", "work_date", name="uq_work_logs_project_user_date"))

quality_reviews = table("quality_reviews",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("task_id", ForeignKey("tasks.id", ondelete="SET NULL")), Column("reviewer_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("reviewee_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("score", Float, nullable=False),
    Column("comment", Text), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False),
    UniqueConstraint("project_id", "task_id", "reviewer_id", "reviewee_id", name="uq_quality_reviews_scope"))

task_checkins = table("task_checkins",
    Column("id", Integer, primary_key=True), Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False), Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("content", Text, nullable=False), Column("hours", Float, nullable=False), Column("blockers", Text), Column("created_at", String(40), nullable=False))

task_reviews = table("task_reviews",
    Column("id", Integer, primary_key=True), Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("reviewer_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("quality", Float, nullable=False),
    Column("comment", Text), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False))

task_review_history = table("task_review_history",
    Column("id", Integer, primary_key=True), Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
    Column("reviewer_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False), Column("quality", Float, nullable=False),
    Column("comment", Text), Column("created_at", String(40), nullable=False), Column("updated_at", String(40)))

agent_memory = table("agent_memory",
    Column("id", Integer, primary_key=True), Column("project_id", Integer, nullable=False),
    Column("session_id", String(100), nullable=False, server_default=text("'default'")), Column("role", String(30), nullable=False),
    Column("content", Text, nullable=False), Column("created_at", String(40), nullable=False), Column("user_id", ForeignKey("users.id", ondelete="SET NULL")))

platform_connections = table("platform_connections",
    Column("id", Integer, primary_key=True), Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("platform", String(50), nullable=False), Column("external_account_id", String(255)), Column("credentials_ref", Text),
    Column("status", String(30), nullable=False, server_default=text("'active'")), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False))

project_integrations = table("project_integrations",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("connection_id", ForeignKey("platform_connections.id", ondelete="CASCADE"), nullable=False), Column("platform", String(50), nullable=False),
    Column("config", Text, nullable=False, server_default=text("'{}'")), Column("enabled", Integer, nullable=False, server_default=text("1")),
    Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False))

external_events = table("external_events",
    Column("id", Integer, primary_key=True), Column("integration_id", ForeignKey("project_integrations.id", ondelete="CASCADE"), nullable=False),
    Column("external_id", String(255), nullable=False), Column("event_type", String(100)), Column("payload", Text, nullable=False, server_default=text("'{}'")),
    Column("occurred_at", String(40)), Column("created_at", String(40), nullable=False),
    UniqueConstraint("integration_id", "external_id", name="uq_external_events_integration_external"))

sync_jobs = table("sync_jobs",
    Column("id", Integer, primary_key=True), Column("integration_id", ForeignKey("project_integrations.id", ondelete="CASCADE"), nullable=False),
    Column("status", String(30), nullable=False, server_default=text("'pending'")), Column("cursor", Text), Column("error", Text),
    Column("started_at", String(40)), Column("finished_at", String(40)), Column("created_at", String(40), nullable=False))

agent_sessions = table("agent_sessions",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("user_id", ForeignKey("users.id", ondelete="SET NULL")), Column("session_key", String(100), nullable=False),
    Column("title", String(255)), Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False),
    UniqueConstraint("project_id", "session_key", name="uq_agent_sessions_project_key"))

agent_messages = table("agent_messages",
    Column("id", Integer, primary_key=True), Column("session_id", ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(30), nullable=False), Column("content", Text, nullable=False), Column("created_at", String(40), nullable=False))

recommendations = table("recommendations",
    Column("id", Integer, primary_key=True), Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("task_id", ForeignKey("tasks.id", ondelete="SET NULL")), Column("task_name", String(200)),
    Column("generated_by", ForeignKey("users.id", ondelete="SET NULL")), Column("payload", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", String(40), nullable=False))

Index("idx_users_email", User.email)
Index("idx_memberships_user", Membership.user_id, Membership.project_id)
Index("idx_tasks_project", Task.project_id, Task.deleted_at, Task.status)
Index("idx_checkins_project", task_checkins.c.project_id, task_checkins.c.created_at)
Index("idx_contributions_project", contributions.c.project_id, contributions.c.deleted_at, contributions.c.status)
Index("idx_audit_project_time", AuditLog.project_id, AuditLog.created_at)
Index("idx_agent_memory_project", agent_memory.c.project_id, agent_memory.c.session_id, agent_memory.c.id)
