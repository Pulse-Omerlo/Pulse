# AI contribution: 50% or more AI-generated
"""Sign in, sign out, and who is signed in."""

import os

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.dependencies import SESSION_COOKIE, CurrentUser, Db
from app.core.errors import NotAuthenticatedError, TooManyAttemptsError
from app.core.rate_limit import AttemptLimiter
from app.services.auth import (
    SignedIn,
    check_password,
    end_session,
    normalize_email,
    session_hours,
    signed_in,
    start_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Failed sign-ins per client address and per email.
login_attempts = AttemptLimiter()


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    email: str
    tenant_id: str
    tenant_name: str


def user_out(user: SignedIn) -> UserOut:
    return UserOut(email=user.email, tenant_id=str(user.tenant_id), tenant_name=user.tenant_name)


def secure_cookie() -> bool:
    """Secure by default; SESSION_COOKIE_SECURE=false only for plain-HTTP setups."""
    return os.environ.get("SESSION_COOKIE_SECURE", "true").strip().lower() != "false"


@router.post("/login", response_model=UserOut)
def login(credentials: Credentials, request: Request, response: Response, db: Db):
    email = normalize_email(credentials.email)
    keys = (f"ip:{request.client.host if request.client else 'unknown'}", f"email:{email}")
    wait = login_attempts.retry_after(*keys)
    if wait:
        raise TooManyAttemptsError(
            "Too many failed sign-ins. Try again later.", details={"retry_after": wait}
        )
    user = check_password(db, email, credentials.password)
    if user is None:
        login_attempts.failed(*keys)
        raise NotAuthenticatedError("Email or password is incorrect")
    login_attempts.succeeded(keys[1])
    token = start_session(db, user)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(session_hours() * 3600),
        httponly=True,
        secure=secure_cookie(),
        # The UI calls the API on the same site, so the cookie never goes cross-site.
        samesite="strict",
        path="/",
    )
    signed = signed_in(db, token)
    assert signed is not None
    return user_out(signed)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Db):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        end_session(db, token)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user_out(user)
