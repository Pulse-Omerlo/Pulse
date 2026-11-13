# AI contribution: 50% or more AI-generated
"""Signing in, and every tenant route refusing requests that are not the tenant's own."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.dependencies import SESSION_COOKIE
from app.core.rate_limit import MAX_FAILURES
from app.core.tenancy import system_access
from app.main import app
from app.models import Tenant, User, UserSession
from app.services.auth import start_session
from tests.integration.signin import PASSWORD, add_user, client_for, sign_in

pytestmark = pytest.mark.integration


def tenant_routes() -> list[tuple[str, str]]:
    """Every (method, path) under /api/tenants/{tenant_id}, from the app itself."""
    paths = app.openapi()["paths"]
    return sorted(
        (method, path)
        for path, operations in paths.items()
        if "{tenant_id}" in path
        for method in operations
    )


def filled(path: str, tenant_id: uuid.UUID) -> str:
    path = path.replace("{tenant_id}", str(tenant_id))
    return re.sub(r"\{[a-z_]+\}", lambda _: str(uuid.uuid4()), path)


@pytest.fixture
def tenants(session):
    with system_access(session):
        own, other = Tenant(name="Own network"), Tenant(name="Other network")
        session.add_all([own, other])
        session.commit()
    return own, other


@pytest.fixture
def client(session):
    with client_for(session) as c:
        yield c


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.mark.parametrize(("method", "path"), tenant_routes())
def test_a_tenant_route_without_a_session_answers_401(client, tenants, method, path):
    # Arrange
    own, _ = tenants

    # Act
    response = client.request(method, filled(path, own.id))

    # Assert
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize(("method", "path"), tenant_routes())
def test_another_tenants_route_answers_404_like_an_unknown_tenant(
    client, session, tenants, method, path
):
    # Arrange
    own, other = tenants
    sign_in(client, session, own.id)

    # Act
    response = client.request(method, filled(path, other.id))
    unknown = client.request(method, filled(path, uuid.uuid4()))

    # Assert
    assert response.status_code == unknown.status_code == 404
    assert response.json()["error"]["message"] == unknown.json()["error"]["message"]


@pytest.mark.parametrize(("method", "path"), tenant_routes())
def test_an_expired_session_answers_401(client, session, tenants, method, path):
    # Arrange
    own, _ = tenants
    email = add_user(session, own.id)
    with system_access(session):
        user = session.scalar(select(User).where(User.email == email))
    token = start_session(session, user, now=datetime.now(UTC) - timedelta(days=2))
    session.commit()
    client.cookies.set(SESSION_COOKIE, token)

    # Act
    response = client.request(method, filled(path, own.id))

    # Assert
    assert response.status_code == 401


def test_a_signed_in_user_reads_their_own_tenant(client, session, tenants):
    # Arrange
    own, _ = tenants
    sign_in(client, session, own.id)

    # Act
    runs = client.get(f"/api/tenants/{own.id}/runs")
    listed = client.get("/api/tenants").json()
    me = client.get("/api/auth/me").json()

    # Assert
    assert runs.status_code == 200
    assert [t["id"] for t in listed] == [str(own.id)]
    assert (me["tenant_id"], me["tenant_name"]) == (str(own.id), "Own network")


def test_listing_tenants_needs_a_session(client, tenants):
    # Act / Assert
    assert client.get("/api/tenants").status_code == 401


def test_the_session_cookie_is_http_only_strict_and_secure(client, session, tenants):
    # Arrange
    own, _ = tenants
    email = add_user(session, own.id)

    # Act
    response = login(client, email)

    # Assert
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    token = response.cookies[SESSION_COOKIE]
    with system_access(session):
        stored = session.scalars(select(UserSession.token_hash)).all()
    assert token not in stored


def test_passwords_are_stored_as_argon2id_hashes(session, tenants):
    # Arrange
    own, _ = tenants

    # Act
    email = add_user(session, own.id)

    # Assert
    with system_access(session):
        stored = session.scalar(select(User.password_hash).where(User.email == email))
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(client, session, tenants):
    # Arrange
    own, _ = tenants
    email = add_user(session, own.id)

    # Act
    wrong = login(client, email, "not the password")
    unknown = login(client, "nobody@pulse.test")

    # Assert
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert SESSION_COOKIE not in wrong.cookies


def test_the_email_is_matched_without_case(client, session, tenants):
    # Arrange
    own, _ = tenants
    email = add_user(session, own.id)

    # Act / Assert
    assert login(client, email.upper()).status_code == 200


def test_repeated_failures_are_refused_even_with_the_right_password(client, session, tenants):
    # Arrange
    own, _ = tenants
    email = add_user(session, own.id)
    for _ in range(MAX_FAILURES):
        login(client, email, "guess")

    # Act
    response = login(client, email)

    # Assert
    assert response.status_code == 429
    assert response.json()["error"]["details"]["retry_after"] > 0


def test_signing_out_ends_the_session(client, session, tenants):
    # Arrange
    own, _ = tenants
    sign_in(client, session, own.id)
    token = client.cookies[SESSION_COOKIE]

    # Act
    response = client.post("/api/auth/logout")

    # Assert
    assert response.status_code == 204
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401
