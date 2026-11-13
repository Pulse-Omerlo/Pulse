# AI contribution: 50% or more AI-generated
import pytest

from app.api.auth import secure_cookie
from app.services.auth import normalize_email, session_hours, token_hash


def test_sessions_last_twelve_hours_unless_configured(monkeypatch):
    # Arrange
    monkeypatch.delenv("SESSION_HOURS", raising=False)

    # Act / Assert
    assert session_hours() == 12
    monkeypatch.setenv("SESSION_HOURS", "1.5")
    assert session_hours() == 1.5


def test_a_session_length_must_be_positive(monkeypatch):
    # Arrange
    monkeypatch.setenv("SESSION_HOURS", "0")

    # Act / Assert
    with pytest.raises(ValueError):
        session_hours()


def test_the_cookie_is_secure_unless_turned_off(monkeypatch):
    # Arrange
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)

    # Act / Assert
    assert secure_cookie() is True
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "False")
    assert secure_cookie() is False


def test_emails_are_compared_trimmed_and_lowercased():
    # Act / Assert
    assert normalize_email("  Ana@Example.CA ") == "ana@example.ca"


def test_only_a_hash_of_the_token_is_kept():
    # Act
    digest = token_hash("secret-token")

    # Assert
    assert len(digest) == 64
    assert "secret-token" not in digest
