# AI contribution: 50% or more AI-generated
"""What routes depend on: a database session, the signed-in user, and their tenant."""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import DatabaseNotConfiguredError, get_db
from app.core.errors import DatabaseUnavailableError, NotAuthenticatedError, NotFoundError
from app.core.tenancy import bound_to_tenant
from app.services.auth import SignedIn, signed_in

SESSION_COOKIE = "pulse_session"


def database() -> Iterator[Session]:
    """A session, or a 503 when the database is not configured or cannot be reached."""
    try:
        yield from get_db()
    except (DatabaseNotConfiguredError, OperationalError) as err:
        raise DatabaseUnavailableError("Database unavailable") from err


Db = Annotated[Session, Depends(database)]


def current_user(request: Request, db: Db) -> SignedIn:
    """The user of the session cookie; 401 without a valid, unexpired one."""
    token = request.cookies.get(SESSION_COOKIE)
    user = signed_in(db, token) if token else None
    if user is None:
        raise NotAuthenticatedError("Sign in to continue")
    return user


CurrentUser = Annotated[SignedIn, Depends(current_user)]


def tenant_database(tenant_id: uuid.UUID, db: Db, user: CurrentUser) -> Iterator[Session]:
    """The session bound to the tenant in the path, when it is the signed-in user's.

    Another tenant answers 404 like an unknown one, so its existence is not revealed.
    """
    if tenant_id != user.tenant_id:
        raise NotFoundError("Tenant not found")
    with bound_to_tenant(db, tenant_id):
        yield db


TenantDb = Annotated[Session, Depends(tenant_database)]
