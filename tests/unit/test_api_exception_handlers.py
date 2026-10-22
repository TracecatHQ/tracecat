"""Tests for API exception handler status code and payload mapping."""

from __future__ import annotations

from unittest.mock import Mock

import orjson
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tracecat.api import app as api_app
from tracecat.api.app import (
    _install_scim_exception_handlers,
    authorization_exception_handler,
    scope_denied_exception_handler,
)
from tracecat.api.app import (
    create_app as create_api_app,
)
from tracecat.api.common import (
    query_overflow_exception_handler,
    query_timeout_exception_handler,
    serialize_validation_errors,
    tracecat_exception_handler,
)
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatException,
    TracecatNotFoundError,
    TracecatRLSViolationError,
    TracecatValidationError,
)
from tracecat.executor.action_gateway import app as action_gateway_app
from tracecat.executor.action_gateway.app import create_app as create_action_gateway_app
from tracecat.forms.schemas import FormValidateRequest
from tracecat.query.errors import (
    TracecatQueryOverflowError,
    TracecatQueryTimeoutError,
)


def _build_app(exc: Exception, path: str = "/boom") -> FastAPI:
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

    app.add_api_route(path, boom, methods=["GET"])
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


@pytest.mark.parametrize(
    "exc",
    [
        TracecatAuthorizationError("Cannot delete superuser"),
        ScopeDeniedError(
            required_scopes=["org:member:remove"], missing_scopes=["org:member:remove"]
        ),
        TracecatRLSViolationError(
            "Internal denial", table="secret", operation="SELECT"
        ),
    ],
)
@pytest.mark.parametrize("scim", [False, True])
def test_scim_authorization_envelope_preserves_opaque_denials(
    exc: Exception, scim: bool
) -> None:
    path = "/scim/v2/Users/protected" if scim else "/boom"
    app = _build_app(exc, path)
    _install_scim_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code == 403
    if scim:
        assert response.headers["content-type"] == "application/scim+json"
        assert response.json() == {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "403",
            "detail": "Forbidden",
        }
    else:
        assert response.json() == _get(exc).json()


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("private internal detail"),
        IntegrityError("private SQL", {}, Exception("private value")),
    ],
)
@pytest.mark.parametrize("scim", [False, True])
def test_unexpected_scim_errors_are_sanitized(exc: Exception, scim: bool) -> None:
    path = "/scim/v2/Users" if scim else "/boom"
    app = _build_app(exc, path)
    _install_scim_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code == 500
    detail = "An unexpected error occurred. Please try again later."
    if scim:
        assert response.headers["content-type"] == "application/scim+json"
        assert response.json() == {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": "500",
            "detail": detail,
        }
    else:
        assert response.json() == {"message": detail}


@pytest.mark.parametrize(
    "request_path", ["/scim/v2/Users/missing", "/api/scim/v2/Users/missing"]
)
def test_scim_errors_keep_their_status_under_an_unstripped_root_path(
    request_path: str,
) -> None:
    """The Helm ingress forwards ``/api``; a SCIM 404 must not become a 500."""
    app = _build_app(TracecatNotFoundError("User not found"), "/scim/v2/Users/missing")
    app.root_path = "/api"
    _install_scim_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(request_path)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/scim+json"


@pytest.mark.parametrize("scim", [False, True])
@pytest.mark.parametrize(
    "method,suffix,status_code,detail",
    [
        ("GET", "/missing", 404, "Not Found"),
        ("POST", "/control", 405, "Method Not Allowed"),
        ("GET", "/control", 401, "Unauthorized"),
    ],
)
def test_http_errors_keep_status_and_use_the_correct_envelope(
    scim: bool, method: str, suffix: str, status_code: int, detail: str
) -> None:
    prefix = "/scim/v2" if scim else "/other"
    app = _build_app(
        HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"}),
        f"{prefix}/control",
    )
    _install_scim_exception_handlers(app)
    with TestClient(app) as client:
        response = client.request(method, f"{prefix}{suffix}")
    assert response.status_code == status_code
    if scim:
        assert response.headers["content-type"] == "application/scim+json"
        assert response.json() == {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status": str(status_code),
            "detail": detail,
        }
    else:
        assert response.json() == {"detail": detail}
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    if status_code == 405:
        assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("gateway", [False, True])
def test_validation_errors_leave_the_rejected_input_out_of_logs(
    monkeypatch: pytest.MonkeyPatch, gateway: bool
) -> None:
    # Valid base64 of the right size, but not a PNG: the image validator
    # rejects it, and the error's input is the whole data URI.
    image = "data:image/png;base64," + "A" * 4000
    module = action_gateway_app if gateway else api_app
    logged = Mock()
    monkeypatch.setattr(module, "logger", logged)
    app = FastAPI()
    app.add_exception_handler(
        RequestValidationError, module.validation_exception_handler
    )

    async def validate(params: FormValidateRequest) -> None:
        return None

    app.add_api_route("/validate", validate, methods=["POST"])
    option = {"value": "a", "image": image}
    body = {"config": {"layout": [{"field": "model", "options": [option]}]}}
    with TestClient(app) as client:
        response = client.post("/validate", json=body)

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["msg"] == "Value error, Image content is not a valid png file"
    assert error["loc"][-1] == "image"
    logged.error.assert_called_once()
    assert "A" * 100 not in str(logged.error.call_args)
    # The executor keeps a gateway error with the run, so it is left out there too.
    assert ("input" in error) is not gateway


def test_validation_errors_replace_surrogates() -> None:
    errors = [
        {
            "type": "value_error",
            "loc": ("body", "a\ud800"),
            "msg": "bad",
            "input": "a\udfff",
            "ctx": {"error": ValueError("b\ud800")},
        }
    ]
    serialized = serialize_validation_errors(errors)
    assert orjson.loads(orjson.dumps(serialized)) == [
        {
            "type": "value_error",
            "loc": ["body", "a\ufffd"],
            "msg": "bad",
            "input": "a\ufffd",
            "ctx": {"error": "b\ufffd"},
        }
    ]
