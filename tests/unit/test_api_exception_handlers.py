"""Tests for API exception handler status code and payload mapping."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat.api.app import (
    authorization_exception_handler,
    scope_denied_exception_handler,
)
from tracecat.api.app import (
    create_app as create_api_app,
)
from tracecat.api.common import (
    query_overflow_exception_handler,
    query_timeout_exception_handler,
    tracecat_exception_handler,
)
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatException,
    TracecatRLSViolationError,
    TracecatValidationError,
)
from tracecat.executor.action_gateway.app import create_app as create_action_gateway_app
from tracecat.query.errors import (
    TracecatQueryOverflowError,
    TracecatQueryTimeoutError,
)


def _build_app(exc: Exception) -> FastAPI:
    """Build an app registering the same handlers as the real API.

    Mirrors create_app() so subtype dispatch is exercised as in production:
    Starlette resolves handlers along the exception MRO, so a subtype with its
    own handler must win over the generic authorization handler.
    """
    app = FastAPI()
    app.add_exception_handler(TracecatException, tracecat_exception_handler)
    app.add_exception_handler(
        TracecatQueryTimeoutError,
        query_timeout_exception_handler,
    )
    app.add_exception_handler(
        TracecatQueryOverflowError,
        query_overflow_exception_handler,
    )
    app.add_exception_handler(
        TracecatAuthorizationError, authorization_exception_handler
    )
    app.add_exception_handler(ScopeDeniedError, scope_denied_exception_handler)

    async def boom() -> None:
        raise exc

    app.add_api_route("/boom", boom, methods=["GET"])
    return app


def _get(exc: Exception):
    with TestClient(_build_app(exc), raise_server_exceptions=False) as client:
        return client.get("/boom")


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        pytest.param(
            TracecatAuthorizationError("denied"), 403, id="authorization-error-403"
        ),
        pytest.param(
            ScopeDeniedError(
                required_scopes=["org:rbac:update"], missing_scopes=["org:rbac:update"]
            ),
            403,
            id="scope-denied-subclass-403",
        ),
        pytest.param(
            TracecatRLSViolationError(
                "RLS blocked", table="secret", operation="SELECT"
            ),
            403,
            id="rls-subclass-403",
        ),
        pytest.param(
            TracecatValidationError("bad"), 500, id="other-tracecat-error-500"
        ),
        pytest.param(TracecatQueryTimeoutError(), 422, id="query-timeout-422"),
        pytest.param(TracecatQueryOverflowError(), 400, id="query-overflow-400"),
    ],
)
def test_exception_handler_status_codes(exc: Exception, expected_status: int) -> None:
    """Authorization denials return 403, not the generic 500."""
    assert _get(exc).status_code == expected_status


def test_authorization_response_is_fixed_and_opaque() -> None:
    """The generic 403 body must not echo the exception message."""
    response = _get(
        TracecatAuthorizationError(
            "Cannot grant scopes not held by the caller: org:owner:assign"
        )
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_rls_violation_does_not_leak_internal_state() -> None:
    """RLS denials must not expose table, operation, or tenant identifiers.

    TracecatRLSViolationError puts these on ``detail``; serializing that on a
    403 would hand an unauthorized caller schema and tenant information.
    """
    response = _get(
        TracecatRLSViolationError(
            "RLS blocked access",
            table="secret",
            operation="SELECT",
            org_id="org-abc-123",
            workspace_id="ws-xyz-789",
        )
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}

    body = response.text
    for leaked in ("secret", "SELECT", "org-abc-123", "ws-xyz-789", "RLS"):
        assert leaked not in body


def test_scope_denied_keeps_its_structured_body() -> None:
    """The allowlisted subtype handler still returns machine-readable detail."""
    response = _get(
        ScopeDeniedError(
            required_scopes=["org:rbac:update"], missing_scopes=["org:rbac:update"]
        )
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "insufficient_scope"
    assert error["missing_scopes"] == ["org:rbac:update"]


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_code"),
    [
        pytest.param(
            TracecatQueryTimeoutError(),
            422,
            "query_timeout",
            id="timeout",
        ),
        pytest.param(
            TracecatQueryOverflowError(),
            400,
            "query_numeric_overflow",
            id="overflow",
        ),
    ],
)
def test_query_error_response_contract(
    exc: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    response = _get(exc)

    assert response.status_code == expected_status
    body = response.json()
    assert body["type"] == type(exc).__name__
    assert body["message"] == str(exc)
    assert body["detail"] == {
        "code": expected_code,
        "message": str(exc),
    }


def test_query_error_handlers_are_registered_in_both_api_apps() -> None:
    for app in (create_api_app(), create_action_gateway_app()):
        assert (
            app.exception_handlers[TracecatQueryTimeoutError]
            is query_timeout_exception_handler
        )
        assert (
            app.exception_handlers[TracecatQueryOverflowError]
            is query_overflow_exception_handler
        )
