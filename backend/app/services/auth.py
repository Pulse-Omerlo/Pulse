# AI contribution: 50% or more AI-generated
"""Users, password checks and sign-in sessions.

Passwords are hashed with Argon2id. A session is a random token kept in an HttpOnly
cookie; the database stores only its SHA-256, so a leaked table cannot be replayed.
Users and sessions are found across tenants (the tenant is not known before sign-in),
so these functions use system access and never return another user's data.
"""

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import DataValidationError
from app.core.tenancy import system_access
from app.models import Tenant, User, UserSession

DEFAULT_SESSION_HOURS = 12
MIN_PASSWORD_LENGTH = 12

hasher = PasswordHasher()
# Checked when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class SignedIn:
    """Who a request is from: the only source of the tenant for routes that need one."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    tenant_name: str


def session_hours() -> float:
    raw = os.environ.get("SESSION_HOURS")
    hours = float(raw) if raw else DEFAULT_SESSION_HOURS
    if hours <= 0:
        raise ValueError("SESSION_HOURS must be positive")
    return hours


def normalize_email(email: str) -> str:
    return email.strip().lower()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(session: Session, tenant_id: uuid.UUID, email: str, password: str) -> User:
    """Add a user to a tenant; the caller commits."""
    email = normalize_email(email)
    if "@" not in email:
        raise DataValidationError("Enter a valid email address")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise DataValidationError(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters")
    with system_access(session):
        if session.scalar(select(User.id).where(User.email == email)) is not None:
            raise DataValidationError("A user with this email already exists")
        user = User(tenant_id=tenant_id, email=email, password_hash=hasher.hash(password))
        session.add(user)
        session.flush()
    return user


def check_password(session: Session, email: str, password: str) -> User | None:
    """The user with this email and password, or None. Upgrades an outdated hash."""
    with system_access(session):
        user = session.scalar(select(User).where(User.email == normalize_email(email)))
        try:
            hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return None
        if user is None:
            return None
        if hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hasher.hash(password)
        return user


def start_session(session: Session, user: User, now: datetime | None = None) -> str:
    """A new session for the user; returns the token to put in the cookie."""
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    with system_access(session):
        session.add(
            UserSession(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=token_hash(token),
                created_at=now,
                expires_at=now + timedelta(hours=session_hours()),
            )
        )
        session.flush()
    return token


def signed_in(session: Session, token: str, now: datetime | None = None) -> SignedIn | None:
    """Who holds this token, or None when it is unknown or expired."""
    now = now or datetime.now(UTC)
    with system_access(session):
        row = session.execute(
            select(UserSession, User, Tenant)
            .join(
                User,
                (User.tenant_id == UserSession.tenant_id) & (User.id == UserSession.user_id),
            )
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(UserSession.token_hash == token_hash(token))
        ).first()
        if row is None:
            return None
        user_session, user, tenant = row
        if user_session.expires_at <= now:
            return None
        return SignedIn(
            user_id=user.id, tenant_id=user.tenant_id, email=user.email, tenant_name=tenant.name
        )


def end_session(session: Session, token: str) -> None:
    with system_access(session):
        session.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))
