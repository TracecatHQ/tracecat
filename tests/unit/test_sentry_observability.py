from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from temporalio.exceptions import ActivityError, ApplicationError

from tracecat.dsl.interceptor import (
    _should_capture_activity_exception,
    _should_capture_workflow_exception,
)
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


def test_sentry_scrubber_redacts_request_query_string_secrets() -> None:
    event = {
        "request": {
            "query_string": "code=secret-code&state=secret-state&safe=visible",
        }
    }

    scrubbed = sentry_observability._scrub(event)
    request = scrubbed["request"]
    params = parse_qs(request["query_string"])

    assert params == {
        "code": [sentry_observability.REDACTED_VALUE],
        "state": [sentry_observability.REDACTED_VALUE],
        "safe": ["visible"],
    }


def test_sentry_scrubber_redacts_semicolon_delimited_query_secrets() -> None:
    event = {
        "request": {
            "query_string": "safe=visible;token=secret-token;code=secret-code",
        }
    }

    scrubbed = sentry_observability._scrub(event)
    request = scrubbed["request"]
    params = parse_qs(request["query_string"])

    assert params == {
        "safe": ["visible"],
        "token": [sentry_observability.REDACTED_VALUE],
        "code": [sentry_observability.REDACTED_VALUE],
    }
    assert "secret-token" not in request["query_string"]
    assert "secret-code" not in request["query_string"]


def test_sentry_scrubber_redacts_request_url_query_secrets() -> None:
    event = {
        "request": {
            "url": "https://example.com/auth/oauth/callback?code=secret-code&state=secret-state&safe=visible",
        }
    }

    scrubbed = sentry_observability._scrub(event)
    request = scrubbed["request"]
    parsed_url = urlsplit(request["url"])
    params = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "example.com"
    assert parsed_url.path == "/auth/oauth/callback"
    assert params == {
        "code": [sentry_observability.REDACTED_VALUE],
        "state": [sentry_observability.REDACTED_VALUE],
        "safe": ["visible"],
    }


def test_sentry_scrubber_removes_request_url_userinfo_and_fragments() -> None:
    event = {
        "request": {
            "url": "https://user:password@example.com/auth/oauth/callback?code=secret-code&safe=visible#access_token=secret-token",
        }
    }

    scrubbed = sentry_observability._scrub(event)
    request = scrubbed["request"]
    parsed_url = urlsplit(request["url"])
    params = parse_qs(parsed_url.query)

    assert parsed_url.scheme == "https"
    assert parsed_url.netloc == "example.com"
    assert parsed_url.path == "/auth/oauth/callback"
    assert parsed_url.fragment == ""
    assert params == {
        "code": [sentry_observability.REDACTED_VALUE],
        "safe": ["visible"],
    }
    assert "password" not in request["url"]
    assert "secret-token" not in request["url"]


@pytest.mark.parametrize(
    ("url", "expected_path"),
    [
        (
            "https://api.example.com/webhooks/wf-test-webhook/super-secret/draft?foo=bar",
            f"/webhooks/wf-test-webhook/{sentry_observability.REDACTED_VALUE}/draft",
        ),
        (
            "https://api.example.com/api/webhooks/wf-test-webhook/super-secret/draft?foo=bar",
            f"/api/webhooks/wf-test-webhook/{sentry_observability.REDACTED_VALUE}/draft",
        ),
    ],
)
def test_sentry_scrubber_redacts_webhook_secret_url_paths(
    url: str, expected_path: str
) -> None:
    event = {
        "request": {
            "url": url,
        }
    }

    scrubbed = sentry_observability._scrub(event)
    request = scrubbed["request"]
    parsed_url = urlsplit(request["url"])

    assert parsed_url.path == expected_path
    assert "super-secret" not in request["url"]


def test_sentry_scrubber_leaves_malformed_request_urls_unchanged() -> None:
    event = {
        "request": {
            "url": "https://[invalid-ipv6-host/auth/oauth/callback?code=secret-code",
        }
    }

    scrubbed = sentry_observability._scrub(event)

    assert (
        scrubbed["request"]["url"]
        == "https://[invalid-ipv6-host/auth/oauth/callback?code=secret-code"
    )


def test_activity_interceptor_skips_known_user_facing_application_errors() -> None:
    exc = ApplicationError("action failed", type="ExecutionError")

    assert _should_capture_activity_exception(exc) is False


def test_activity_interceptor_skips_base_tracecat_application_errors() -> None:
    exc = ApplicationError("user-facing failure", type="TracecatException")

    assert _should_capture_activity_exception(exc) is False


def test_activity_interceptor_skips_untyped_application_errors() -> None:
    exc = ApplicationError("Invalid custom model provider credentials")

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


def test_workflow_interceptor_skips_user_facing_activity_error_causes() -> None:
    exc = _activity_error_with_cause(
        ApplicationError("action failed", type="ExecutionError")
    )

    assert _should_capture_workflow_exception(exc) is False


def test_workflow_interceptor_captures_platform_activity_error_causes() -> None:
    exc = _activity_error_with_cause(
        ApplicationError("database failed", type="OSError")
    )

    assert _should_capture_workflow_exception(exc) is True


def _activity_error_with_cause(cause: Exception) -> ActivityError:
    exc = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test-worker",
        activity_type="test_activity",
        activity_id="test-activity-id",
        retry_state=None,
    )
    exc.__cause__ = cause
    return exc
