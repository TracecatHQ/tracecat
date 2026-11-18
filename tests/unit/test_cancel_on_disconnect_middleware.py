"""The API cancels abandoned case searches through an outermost ASGI layer."""

import asyncio
from typing import Any

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import Message

from tracecat.api.app import app
from tracecat.cases.router import CASE_SEARCH_PATH_SUFFIXES
from tracecat.middleware import RequestLoggingMiddleware
from tracecat.middleware.disconnect import CancelOnDisconnectMiddleware


def test_api_installs_disconnect_cancellation_outside_http_middleware() -> None:
    classes: list[object] = [middleware.cls for middleware in app.user_middleware]
    # user_middleware lists the outermost layer first.
    position = classes.index(CancelOnDisconnectMiddleware)
    assert position < classes.index(RequestLoggingMiddleware)
    assert position < classes.index(CORSMiddleware)
    options = app.user_middleware[position].kwargs
    assert options["path_suffixes"] == CASE_SEARCH_PATH_SUFFIXES
    paths = set(app.openapi()["paths"])
    for suffix in CASE_SEARCH_PATH_SUFFIXES:
        assert any(path.endswith(suffix) for path in paths), suffix


def _scope(path: str, method: str = "POST") -> dict[str, Any]:
    return {"type": "http", "method": method, "path": path, "headers": []}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "method"),
    [("/cases/search", "GET"), ("/cases/other", "POST"), ("/cases/search/x", "POST")],
)
async def test_other_requests_pass_through_unchanged(path: str, method: str) -> None:
    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: Message) -> None:
        return None

    seen: list[object] = []

    async def app(_scope: Any, app_receive: Any, app_send: Any) -> None:
        seen.extend([app_receive, app_send])

    middleware = CancelOnDisconnectMiddleware(
        app, path_suffixes=CASE_SEARCH_PATH_SUFFIXES
    )
    await middleware(_scope(path, method), receive, send)
    assert seen == [receive, send]


@pytest.mark.anyio
async def test_disconnect_before_the_body_never_calls_the_app() -> None:
    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(_message: Message) -> None:
        raise AssertionError("nothing should be sent")

    async def app(_scope: Any, _receive: Any, _send: Any) -> None:
        raise AssertionError("the app should not run")

    middleware = CancelOnDisconnectMiddleware(
        app, path_suffixes=CASE_SEARCH_PATH_SUFFIXES
    )
    await middleware(_scope("/workspaces/w/cases/search"), receive, send)


@pytest.mark.anyio
async def test_work_after_a_complete_response_is_not_cancelled() -> None:
    chunks = [
        {"type": "http.request", "body": b'{"a":', "more_body": True},
        {"type": "http.request", "body": b"1}", "more_body": False},
    ]

    async def receive() -> Message:
        if chunks:
            return chunks.pop(0)
        # The server reports a disconnect once the response is complete.
        return {"type": "http.disconnect"}

    async def send(_message: Message) -> None:
        return None

    events: list[str] = []

    async def app(_scope: Any, app_receive: Any, app_send: Any) -> None:
        body = b""
        while True:
            message = await app_receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        events.append(body.decode())
        await app_send({"type": "http.response.start", "status": 200, "headers": []})
        await app_send({"type": "http.response.body", "body": b"ok"})
        await asyncio.sleep(0.05)  # e.g. after-response logging
        events.append("after response")

    middleware = CancelOnDisconnectMiddleware(
        app, path_suffixes=CASE_SEARCH_PATH_SUFFIXES
    )
    await middleware(_scope("/cases/search/aggregate"), receive, send)
    assert events == ['{"a":1}', "after response"]
