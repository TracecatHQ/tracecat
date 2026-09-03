import orjson
import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request

from tracecat.api.common import (
    auth_pool_exhausted_exception_handler,
    generic_exception_handler,
)
from tracecat.db.exceptions import AuthPoolExhaustedError


@pytest.mark.anyio
async def test_generic_exception_handler_logs_with_exception(mocker):
    logger = mocker.patch("tracecat.api.common.logger")
    capture_platform_failure = mocker.patch(
        "tracecat.api.common.capture_platform_failure"
    )
    exc = RuntimeError("boom")

    async def endpoint() -> None:
        return None

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/workspaces/synthetic-workspace/boom",
            "query_string": b"foo=bar",
            "headers": [],
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
            "route": APIRoute(
                "/workspaces/{workspace_id}/boom",
                endpoint=endpoint,
            ),
        }
    )

    response = await generic_exception_handler(request, exc)

    capture_platform_failure.assert_called_once()
    captured_error, classification, context = capture_platform_failure.call_args.args
    assert captured_error is exc
    assert classification.kind.value == "api.request.unhandled"
    assert classification.owner.value == "platform"
    assert context.method == "GET"
    assert context.route == "/workspaces/{workspace_id}/boom"
    logger.exception.assert_called_once()
    logger.error.assert_not_called()
    args, kwargs = logger.exception.call_args
    assert args == ("Unexpected error",)
    assert kwargs["exc"] is exc
    assert kwargs["path"] == "/workspaces/synthetic-workspace/boom"
    assert str(kwargs["params"]) == "foo=bar"
    assert response.status_code == 500


def test_auth_pool_exhaustion_handler_returns_machine_readable_503(mocker) -> None:
    logger = mocker.patch("tracecat.api.common.logger")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/login",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )

    response = auth_pool_exhausted_exception_handler(
        request,
        AuthPoolExhaustedError("pool timeout"),
    )

    assert response.status_code == 503
    assert orjson.loads(response.body) == {
        "detail": {
            "code": "auth_database_unavailable",
            "message": (
                "Authentication database capacity is temporarily unavailable. "
                "Please retry."
            ),
        }
    }
    logger.error.assert_called_once()
