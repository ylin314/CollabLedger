from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)
    status: str = "offline"
    password: Optional[str] = Field(default=None, min_length=8)


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    skills: Optional[list[str]] = None
    max_concurrent_tasks: Optional[int] = Field(default=None, ge=1, le=100)
    status: Optional[Literal["online", "offline", "busy"]] = None


class MentorIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=254)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=5000)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    owner_id: Optional[int] = None
    mentors: list[MentorIn] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    project_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=5000)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class MemberIn(BaseModel):
    user_id: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    role: Literal["member", "viewer"] = "member"
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)


class RoleUpdate(BaseModel):
    role: Literal["owner", "member", "viewer"]


class InvitationIn(BaseModel):
    role: Literal["member", "viewer"] = "member"
    expires_in_hours: int = Field(default=168, ge=1, le=24 * 365)
    max_uses: int = Field(default=10, ge=1, le=10000)
    email: Optional[str] = None
    expires_days: Optional[int] = Field(default=None, ge=1, le=365)
    is_mentor: bool = False


class AcceptInvitationIn(BaseModel):
    token: Optional[str] = None
    invite_code: Optional[str] = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    assignee_id: Optional[int] = None
    reviewer_id: Optional[int] = None
    task_type: Optional[str] = Field(default=None, max_length=100)
    priority: Literal["low", "medium", "high"] = "medium"
    due_date: Optional[date] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    status: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    assignee_id: Optional[int] = None
    reviewer_id: Optional[int] = None
    task_type: Optional[str] = Field(default=None, max_length=100)
    priority: Optional[Literal["low", "medium", "high"]] = None
    due_date: Optional[date] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    actual_hours: Optional[float] = Field(default=None, ge=0)
    quality: Optional[float] = Field(default=None, ge=0, le=5)
    status: Optional[str] = None
    user_id: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=1000)


class AssignIn(BaseModel):
    assignee_id: int
    note: Optional[str] = Field(default=None, max_length=1000)


class TaskActionIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    actual_hours: Optional[float] = Field(default=None, ge=0)


class CheckinIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    hours: float = Field(ge=0, le=24)
    blockers: Optional[str] = Field(default=None, max_length=1000)


class ReviewIn(BaseModel):
    quality: float = Field(ge=0, le=5)
    comment: Optional[str] = Field(default=None, max_length=1000)


class ContributionIn(BaseModel):
    user_id: Optional[int] = None
    kind: Literal["code", "document", "meeting", "research", "test", "design", "other"] = "other"
    title: str = Field(min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    quantity: float = Field(default=1, ge=0)
    evidence_url: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContributionUpdate(BaseModel):
    kind: Optional[Literal["code", "document", "meeting", "research", "test", "design", "other"]] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=5000)
    quantity: Optional[float] = Field(default=None, ge=0)
    evidence_url: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[datetime] = None


class NoteIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class AgentIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=100)


class RecommendBatchIn(BaseModel):
    limit: int = Field(default=3, ge=1, le=20)
    include_owner: bool = False


class RecommendDecideIn(BaseModel):
    user_id: int
    action: Optional[Literal["accept", "manual"]] = None
    note: Optional[str] = Field(default=None, max_length=1000)


class WorkLogIn(BaseModel):
    work_date: Optional[date] = None
    hours: float = Field(default=0, ge=0, le=24)
    note: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None


class QualityReviewIn(BaseModel):
    task_id: Optional[int] = None
    reviewee_id: int
    score: float = Field(ge=0, le=5)
    comment: Optional[str] = None

