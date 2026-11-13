# AI contribution: 50% or more AI-generated
"""A test client signed in as a user of one tenant, through the real sign-in route."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.api.auth import login_attempts
from app.api.dependencies import database
from app.main import app
from app.services.auth import create_user

PASSWORD = "correct horse battery staple"


def add_user(session, tenant_id: uuid.UUID, email: str | None = None) -> str:
    email = email or f"user-{uuid.uuid4().hex[:8]}@pulse.test"
    create_user(session, tenant_id, email, PASSWORD)
    session.commit()
    return email


@contextmanager
def client_for(session) -> Iterator[TestClient]:
    """A client on the test session; https so the Secure session cookie is sent back."""
    app.dependency_overrides[database] = lambda: session
    login_attempts.reset()
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        login_attempts.reset()


def sign_in(client: TestClient, session, tenant_id: uuid.UUID) -> str:
    email = add_user(session, tenant_id)
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return email


@contextmanager
def signed_in_client(session, tenant_id: uuid.UUID) -> Iterator[TestClient]:
    with client_for(session) as client:
        sign_in(client, session, tenant_id)
        yield client
