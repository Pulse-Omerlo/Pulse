# AI contribution: 50% or more AI-generated
"""Error reporting to Sentry, turned on by setting SENTRY_DSN."""

import os
import sys
from collections.abc import MutableMapping
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

ERROR_METHODS = ("error", "exception", "critical")


class MonitoringConfigError(ValueError):
    """A Sentry setting in the environment is invalid."""


def _logged_exception(exc_info: Any) -> BaseException | None:
    if exc_info is True:
        return sys.exc_info()[1]
    if isinstance(exc_info, BaseException):
        return exc_info
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        return exc_info[1]
    return None


def report_errors_to_sentry(
    _logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor: send errors logged with an exception to Sentry.

    Does nothing until Sentry is started. Only the exception and the log event name
    are sent, not the other log fields.
    """
    error = _logged_exception(event_dict.get("exc_info"))
    if error is not None and method_name in ERROR_METHODS:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("log_event", str(event_dict.get("event")))
            sentry_sdk.capture_exception(error)
    return event_dict


def traces_sample_rate() -> float:
    """Share of requests traced for performance, from SENTRY_TRACES_SAMPLE_RATE (default 0)."""
    raw = os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "").strip() or "0"
    try:
        rate = float(raw)
    except ValueError:
        raise MonitoringConfigError(f"SENTRY_TRACES_SAMPLE_RATE {raw!r} is not a number") from None
    if not 0 <= rate <= 1:
        raise MonitoringConfigError(f"SENTRY_TRACES_SAMPLE_RATE {raw!r} is not between 0 and 1")
    return rate


def configure_sentry() -> bool:
    """Start Sentry when SENTRY_DSN is set and return whether it started.

    Exceptions reach Sentry through report_errors_to_sentry, so failures caught by the
    error middleware and failed sync runs are reported too. Sentry's own log capture is
    off: it would send log fields before they are masked.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "").strip() or "development",
        release=os.environ.get("SENTRY_RELEASE", "").strip() or None,
        # Personal data stays out of events (Law 25): no IP addresses, cookies, bodies
        # or local variables.
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        integrations=[LoggingIntegration(level=None, event_level=None)],
        traces_sample_rate=traces_sample_rate(),
    )
    return True
