# AI contribution: 50% or more AI-generated
import io

import pytest

from app.core import monitoring
from app.core.logging import configure_logging, get_logger
from app.core.monitoring import (
    MonitoringConfigError,
    configure_sentry,
    report_errors_to_sentry,
    traces_sample_rate,
)

SENTRY_VARIABLES = (
    "SENTRY_DSN",
    "SENTRY_ENVIRONMENT",
    "SENTRY_RELEASE",
    "SENTRY_TRACES_SAMPLE_RATE",
)


@pytest.fixture
def init_calls(monkeypatch):
    """Record sentry_sdk.init calls instead of starting Sentry; start from no settings."""
    for name in SENTRY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(monitoring.sentry_sdk, "init", lambda **options: calls.append(options))
    return calls


@pytest.mark.parametrize("dsn", [None, "", "   "])
def test_sentry_stays_off_without_a_dsn(init_calls, monkeypatch, dsn):
    # Arrange
    if dsn is not None:
        monkeypatch.setenv("SENTRY_DSN", dsn)

    # Act
    started = configure_sentry()

    # Assert
    assert started is False
    assert init_calls == []


def test_sentry_starts_with_a_dsn_and_keeps_personal_data_out(init_calls, monkeypatch):
    # Arrange
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
    monkeypatch.setenv("SENTRY_RELEASE", "pulse@0.1.0")

    # Act
    started = configure_sentry()

    # Assert
    assert started is True
    (options,) = init_calls
    assert options["dsn"] == "https://key@sentry.example/1"
    assert options["environment"] == "production"
    assert options["release"] == "pulse@0.1.0"
    assert options["send_default_pii"] is False
    assert options["max_request_body_size"] == "never"
    assert options["include_local_variables"] is False
    assert options["traces_sample_rate"] == 0


def test_sentry_defaults_to_the_development_environment(init_calls, monkeypatch):
    # Arrange
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")

    # Act
    configure_sentry()

    # Assert
    (options,) = init_calls
    assert options["environment"] == "development"
    assert options["release"] is None


@pytest.mark.parametrize(("raw", "expected"), [("0.25", 0.25), ("1", 1.0), (" ", 0.0)])
def test_the_traces_sample_rate_comes_from_the_environment(monkeypatch, raw, expected):
    # Arrange
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", raw)

    # Act / Assert
    assert traces_sample_rate() == expected


@pytest.mark.parametrize("raw", ["fast", "-0.1", "1.5"])
def test_an_invalid_traces_sample_rate_is_refused(monkeypatch, raw):
    # Arrange
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", raw)

    # Act / Assert
    with pytest.raises(MonitoringConfigError, match="SENTRY_TRACES_SAMPLE_RATE"):
        traces_sample_rate()


@pytest.fixture
def captured(monkeypatch):
    """Exceptions passed to sentry_sdk.capture_exception, which is not called for real."""
    errors = []
    monkeypatch.setattr(monitoring.sentry_sdk, "capture_exception", errors.append)
    return errors


def raised(message: str) -> RuntimeError:
    try:
        raise RuntimeError(message)
    except RuntimeError as error:
        return error


@pytest.mark.parametrize("form", ["instance", "tuple"])
def test_an_error_logged_with_an_exception_is_sent(captured, form):
    # Arrange
    error = raised("boom")
    exc_info = error if form == "instance" else (RuntimeError, error, error.__traceback__)

    # Act
    report_errors_to_sentry(None, "error", {"event": "sync_failed", "exc_info": exc_info})

    # Assert
    assert captured == [error]


def test_exc_info_true_sends_the_exception_being_handled(captured):
    # Arrange
    error = raised("boom")

    # Act
    try:
        raise error
    except RuntimeError:
        report_errors_to_sentry(None, "exception", {"event": "failed", "exc_info": True})

    # Assert
    assert captured == [error]


@pytest.mark.parametrize(
    ("method", "event_dict"),
    [
        ("error", {"event": "no_exception"}),
        ("warning", {"event": "only_a_warning", "exc_info": RuntimeError("boom")}),
        ("error", {"event": "nothing_handled", "exc_info": True}),
    ],
)
def test_other_log_calls_are_not_sent(captured, method, event_dict):
    # Act
    returned = report_errors_to_sentry(None, method, event_dict)

    # Assert
    assert captured == []
    assert returned is event_dict


def test_the_app_logger_sends_logged_exceptions(captured):
    # Arrange
    configure_logging(level="INFO", stream=io.StringIO())
    error = raised("boom")

    # Act
    get_logger("tests.monitoring").error("unhandled_error", exc_info=error)
    get_logger("tests.monitoring").info("all_good")

    # Assert
    assert captured == [error]
