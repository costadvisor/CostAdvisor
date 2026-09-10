"""In-app support: a user opens a thread (scoped to themselves or their whole
team), support staff reply — optionally from a canned-response library — and
a separate platform-level FAQ/knowledge-base covers onboarding, team
creation, etc.

Team-scoping follows the AlertSubscription precedent (CLAUDE.md, Scrum 24):
RLS only ever enforces the *team* boundary (a thread's team_id must be one of
the caller's teams); narrowing a "person"-scope thread down to just its
creator is an application-layer filter, not a second RLS predicate — this
codebase has no per-user RLS anywhere, and inventing one is a bigger change
than this feature needs. `SupportCannedResponse`/`SupportFAQ` are platform
reference data (no team_id, no RLS), mirroring `commodity_indexes`.
"""
import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SupportThread(Base):
    __tablename__ = "support_threads"
    __table_args__ = (
        CheckConstraint("scope IN ('person', 'team')", name="ck_support_thread_scope"),
        CheckConstraint(
            "status IN ('open', 'pending', 'resolved', 'closed')",
            name="ck_support_thread_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # 'person' (default): only the creator + support staff see it, even
    # though teammates pass the RLS team check — enforced in the router.
    # 'team': every team member can see and reply in it.
    scope = Column(String(10), nullable=False, server_default="person")
    subject = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False, server_default="open")
    assigned_admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    messages = relationship(
        "SupportMessage", backref="thread", cascade="all, delete-orphan",
        order_by="SupportMessage.created_at",
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Denormalized from the thread (CostModelNote does the same for
    # cost_model_id) so RLS is a direct column check, not a subquery.
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    thread_id = Column(UUID(as_uuid=True), ForeignKey("support_threads.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_staff = Column(Boolean, nullable=False, server_default="false")
    body = Column(Text, nullable=False)
    canned_response_id = Column(
        UUID(as_uuid=True), ForeignKey("support_canned_responses.id", ondelete="SET NULL"), nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SupportCannedResponse(Base):
    """Platform-wide reply templates support staff can insert into a message.
    No team_id: the library is shared across every team's threads, same
    reasoning as commodity_indexes being platform reference data."""
    __tablename__ = "support_canned_responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(150), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(50), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SupportFAQ(Base):
    """Admin-managed knowledge base (onboarding, team creation, etc.) — read
    by every authenticated user, written by support staff. Platform-level,
    same reasoning as SupportCannedResponse."""
    __tablename__ = "support_faqs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category = Column(String(50), nullable=False)
    question = Column(String(300), nullable=False)
    answer = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, server_default="0")
    published = Column(Boolean, nullable=False, server_default="true")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
