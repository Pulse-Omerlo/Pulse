# AI contribution: 50% or more AI-generated
"""PULSE's own users, each in one tenant, and their sign-in sessions."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, same_tenant_fk, tenant_id_column, uuid_pk


class User(Base):
    """Someone who signs in to PULSE. Separate from Omerlo's accounts (D7)."""

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # One account per email across PULSE, so signing in needs no tenant. Emails are
        # stored lowercased, so this is case-insensitive.
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    email: Mapped[str] = mapped_column(Text)
    # Argon2id hash with its own salt and parameters.
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """A signed-in browser. Only a hash of the token in its cookie is stored."""

    __tablename__ = "user_session"
    __table_args__ = (
        same_tenant_fk("user_id", "app_user"),
        UniqueConstraint("token_hash"),
        Index("ix_user_session_user", "tenant_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = tenant_id_column()
    user_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
