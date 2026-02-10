"""Tests for API exception handlers."""

import json

from fastapi import status
from starlette.requests import Request

from tracecat.api.common import tracecat_exception_handler
from tracecat.exceptions import TracecatException


def _build_request(path: str, query_string: str = "") -> Request:
    """Create a minimal ASGI request object for handler tests."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_tracecat_exception_handler_with_braces_in_message() -> None:
    """Messages containing braces should not break logger formatting."""
    request = _build_request(
        "/workflows/wf_123/publish",
        query_string="workspace_id=c7e6b746-88dd-443e-bba5-c14e96db9adb",
    )
    exc = TracecatException(
        "GitHub API error: 404 - {'message': 'Branch not found'}",
        detail={"status": 404},
    )

    response = tracecat_exception_handler(request, exc)
    payload = json.loads(bytes(response.body))

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert payload["type"] == "TracecatException"
    assert payload["message"] == "GitHub API error: 404 - {'message': 'Branch not found'}"
    assert payload["detail"] == {"status": 404}
