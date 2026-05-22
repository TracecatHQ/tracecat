from __future__ import annotations

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.dsl.interceptor import _should_capture_activity_exception
from tracecat.exceptions import TracecatException, TracecatExpressionError
from tracecat.observability import sentry as sentry_observability


def test_init_sentry_noops_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    assert sentry_observability.init_sentry("api") is False


def test_sentry_scrubber_redacts_nested_sensitive_fields() -> None:
    event = {
        "request": {
            "headers": {
                "authorization": "Bearer secret",
                "x-trace-id": "trace-id",
            },
            "cookies": {"session": "secret-cookie"},
        },
        "extra": {
            "payload": {
                "api_key": "secret-key",
                "safe": "kept",
            }
        },
    }

    scrubbed = sentry_observability._scrub(event)

    assert scrubbed == {
        "request": {
            "headers": {
                "authorization": sentry_observability.REDACTED_VALUE,
                "x-trace-id": "trace-id",
            },
            "cookies": sentry_observability.REDACTED_VALUE,
        },
        "extra": {
            "payload": {
                "api_key": sentry_observability.REDACTED_VALUE,
                "safe": "kept",
            }
        },
    }


def test_sentry_scrubber_redacts_camel_case_sensitive_fields() -> None:
    event = {
        "extra": {
            "payload": {
                "apiKey": "secret-key",
                "privateKey": "secret-private-key",
                "safe": "kept",
            }
        }
    }

    scrubbed = sentry_observability._scrub(event)

    assert scrubbed == {
        "extra": {
            "payload": {
                "apiKey": sentry_observability.REDACTED_VALUE,
                "privateKey": sentry_observability.REDACTED_VALUE,
                "safe": "kept",
            }
        }
    }


def test_activity_interceptor_skips_known_user_facing_application_errors() -> None:
    exc = ApplicationError("action failed", type="ExecutionError")

    assert _should_capture_activity_exception(exc) is False


def test_activity_interceptor_skips_base_tracecat_application_errors() -> None:
    exc = ApplicationError("user-facing failure", type="TracecatException")

    assert _should_capture_activity_exception(exc) is False


def test_activity_interceptor_skips_direct_tracecat_exceptions() -> None:
    assert (
        _should_capture_activity_exception(TracecatException("invalid input")) is False
    )
    assert (
        _should_capture_activity_exception(
            TracecatExpressionError("Expression cannot be empty")
        )
        is False
    )


def test_activity_interceptor_captures_platform_application_errors() -> None:
    exc = ApplicationError("database failed", type="RuntimeError")

    assert _should_capture_activity_exception(exc) is True
