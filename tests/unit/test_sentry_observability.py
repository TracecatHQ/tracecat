from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from temporalio import activity
from temporalio.common import Priority, RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

from tracecat.auth.types import system_role
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.interceptor import (
    _activity_info_context,
    _should_capture_activity_exception,
    _should_capture_workflow_exception,
    _workflow_input_context,
)
from tracecat.exceptions import TracecatException, TracecatExpressionError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.observability import sentry as sentry_observability
from tracecat.storage.object import InlineObject
from tracecat.workflow.executions.enums import ExecutionType


def test_init_sentry_noops_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    assert sentry_observability.init_sentry("api") is False


def test_init_sentry_disables_local_variable_capture(
    monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    mocker.patch.object(
        sentry_observability.sentry_sdk, "is_initialized", return_value=False
    )
    init = mocker.patch.object(sentry_observability.sentry_sdk, "init")
    mocker.patch.object(sentry_observability.sentry_sdk, "set_tag")

    assert sentry_observability.init_sentry("api") is True

    assert init.call_args.kwargs["include_local_variables"] is False


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
        (
            "https://api.example.com/webhooks/wf-test-webhook/super-secret/extra/webhooks/foo/bar",
            f"/webhooks/wf-test-webhook/{sentry_observability.REDACTED_VALUE}/extra/webhooks/foo/bar",
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


def test_activity_interceptor_skips_retryable_platform_errors_before_final_attempt() -> (
    None
):
    info = _activity_info(attempt=1, maximum_attempts=3)

    assert (
        _should_capture_activity_exception(RuntimeError("database failed"), info)
        is False
    )


def test_activity_interceptor_captures_retryable_platform_errors_on_final_attempt() -> (
    None
):
    info = _activity_info(attempt=3, maximum_attempts=3)

    assert (
        _should_capture_activity_exception(RuntimeError("database failed"), info)
        is True
    )


def test_activity_interceptor_captures_non_retryable_platform_errors_immediately() -> (
    None
):
    exc = ApplicationError(
        "database failed",
        type="RuntimeError",
        non_retryable=True,
    )
    info = _activity_info(attempt=1, maximum_attempts=3)

    assert _should_capture_activity_exception(exc, info) is True


def test_activity_interceptor_skips_unbounded_retryable_platform_errors() -> None:
    info = _activity_info(attempt=10, maximum_attempts=0)

    assert (
        _should_capture_activity_exception(RuntimeError("database failed"), info)
        is False
    )


def test_activity_info_context_includes_retry_policy_metadata() -> None:
    info = _activity_info(attempt=2, maximum_attempts=6)

    context = _activity_info_context(info)

    assert context["attempt"] == 2
    assert context["retry_policy"] == {
        "initial_interval_seconds": 1.0,
        "backoff_coefficient": 2.0,
        "maximum_interval_seconds": None,
        "maximum_attempts": 6,
        "non_retryable_error_types": None,
    }


def test_workflow_input_context_omits_dsl_run_args_payloads() -> None:
    run_args = DSLRunArgs.model_construct(
        role=system_role(),
        wf_id=WorkflowUUID.new_uuid4(),
        trigger_inputs=InlineObject(
            data={
                "customer_email": "customer@example.com",
                "safe_field": "customer-provided value",
            }
        ),
        dsl=DSLInput.model_construct(
            title="Customer workflow",
            description="Contains user-authored workflow text",
            entrypoint=DSLEntrypoint(ref="start"),
            actions=[],
        ),
        parent_run_context=None,
        schedule_id=None,
        execution_type=ExecutionType.PUBLISHED,
        timeout=timedelta(minutes=5),
        time_anchor=None,
    )

    context = _workflow_input_context(run_args)

    assert context["type"] == "DSLRunArgs"
    assert context["wf_id"] == run_args.wf_id.short()
    assert "trigger_inputs" not in context
    assert "dsl" not in context
    assert "role" not in context
    assert "customer@example.com" not in str(context)
    assert "Customer workflow" not in str(context)


def test_workflow_interceptor_skips_expected_cancellations() -> None:
    assert _should_capture_workflow_exception(CancelledError()) is False


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


def _activity_info(
    *, attempt: int, maximum_attempts: int | None = None
) -> activity.Info:
    now = datetime.now(UTC)
    retry_policy = (
        RetryPolicy(maximum_attempts=maximum_attempts)
        if maximum_attempts is not None
        else None
    )
    return activity.Info(
        activity_id="test-activity-id",
        activity_type="test_activity",
        attempt=attempt,
        current_attempt_scheduled_time=now,
        heartbeat_details=[],
        heartbeat_timeout=None,
        is_local=False,
        schedule_to_close_timeout=None,
        scheduled_time=now,
        start_to_close_timeout=None,
        started_time=now,
        task_queue="test-task-queue",
        task_token=b"test-task-token",
        workflow_id="test-workflow-id",
        workflow_namespace="default",
        workflow_run_id="test-run-id",
        workflow_type="TestWorkflow",
        priority=Priority.default,
        retry_policy=retry_policy,
    )
