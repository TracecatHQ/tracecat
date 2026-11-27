"""URL path secrets stay out of request logs.

Webhook, SNS ingest and invitation URLs carry a secret path segment. These
tests send requests with known synthetic secrets through the real API app and
a real uvicorn server, then check every captured log record at every level.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
from collections.abc import Generator, Sequence
from typing import Annotated

import httpx
import pytest
import uvicorn
from fastapi import HTTPException, Query
from fastapi.routing import APIRoute, _IncludedRouter
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import BaseModel

from tracecat.api.app import create_app
from tracecat.logger.redaction import (
    REDACTED_URL_SECRET,
    SECRET_PATH_PARAMS,
    UVICORN_LOGGER_NAMES,
    URLSecretRedactionFilter,
    install_uvicorn_url_secret_redaction,
    redact_query_string,
    redact_url_secrets,
)
from tracecat.triggers.ingest.aws_sns import router as sns_router
from tracecat.triggers.ingest.types import IngestError, IngestErrorCode
from tracecat.webhooks.dependencies import validate_incoming_webhook

# Synthetic values. The ingest token matches the route's token shape so the
# request reaches the handler instead of the shape check.
INGEST_TOKEN = "SyntheticIngestToken0123456789abcdefghijk"
WEBHOOK_SECRET = "synthetic-webhook-secret-0123456789"
INVITATION_TOKEN = "synthetic-invitation-token-0123456789"
WORKFLOW_ID = "wf_0zLWumDDdMzepLBrQIOwpD"
STATE_VALUE = "synthetic-signed-state-0123456789"
SECRETS = (INGEST_TOKEN, WEBHOOK_SECRET, INVITATION_TOKEN, STATE_VALUE)

INGEST_PATH = f"/ingest/sns/{INGEST_TOKEN}"
WEBHOOK_PATH = f"/webhooks/{WORKFLOW_ID}/{WEBHOOK_SECRET}?state={STATE_VALUE}"

# A private app instance: starting the shared ``tracecat.api.app.app`` builds
# its middleware stack, and later tests that add middleware to it would fail.
app = create_app()

# A new route using one of the SECRET_PATH_PARAMS names fails
# test_every_secret_route_is_redacted until it has a pattern.
_PATH_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")


def _assert_no_secret(lines: list[str]) -> None:
    assert lines, "expected log records to be captured"
    for line in lines:
        for secret in SECRETS:
            assert secret not in line, line


@contextlib.contextmanager
def _captured_loguru() -> Generator[list[str]]:
    """Capture every loguru record, extras and traceback, at every level."""
    lines: list[str] = []
    # backtrace/diagnose off as in the production sinks (tracecat/logger).
    sink_id = logger.add(
        lambda message: lines.append(str(message)),
        level="TRACE",
        format="{level} {message} | {extra}\n{exception}",
        backtrace=False,
        diagnose=False,
    )
    try:
        yield lines
    finally:
        logger.remove(sink_id)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            f"/ingest/sns/{INGEST_TOKEN}",
            f"/ingest/sns/{REDACTED_URL_SECRET}",
            id="ingest",
        ),
        pytest.param(
            f"/api/ingest/sns/{INGEST_TOKEN}?a=1",
            f"/api/ingest/sns/{REDACTED_URL_SECRET}?a=1",
            id="ingest-root-path-query",
        ),
        pytest.param(
            f"/webhooks/{WORKFLOW_ID}/{WEBHOOK_SECRET}",
            f"/webhooks/{WORKFLOW_ID}/{REDACTED_URL_SECRET}",
            id="webhook",
        ),
        pytest.param(
            f"/api/webhooks/{WORKFLOW_ID}/{WEBHOOK_SECRET}/draft",
            f"/api/webhooks/{WORKFLOW_ID}/{REDACTED_URL_SECRET}/draft",
            id="webhook-subpath",
        ),
        pytest.param(
            f"/invitations/token/{INVITATION_TOKEN}",
            f"/invitations/token/{REDACTED_URL_SECRET}",
            id="invitation",
        ),
        pytest.param(
            f'127.0.0.1:1 - "POST /api/webhooks/{WORKFLOW_ID}/{WEBHOOK_SECRET} HTTP/1.1" 200',
            f'127.0.0.1:1 - "POST /api/webhooks/{WORKFLOW_ID}/{REDACTED_URL_SECRET} HTTP/1.1" 200',
            id="request-line",
        ),
        pytest.param(
            "/agent/channels/slack/synthetic-channel-token",
            f"/agent/channels/slack/{REDACTED_URL_SECRET}",
            id="agent-channel",
        ),
        pytest.param(
            "/agent/channel-links/synthetic-link-token/sign-in",
            f"/agent/channel-links/{REDACTED_URL_SECRET}/sign-in",
            id="channel-link",
        ),
        pytest.param(
            "/agent/channels/slack/oauth/callback",
            "/agent/channels/slack/oauth/callback",
            id="channel-oauth-callback",
        ),
        pytest.param(
            "/agent/channel-links/callback",
            "/agent/channel-links/callback",
            id="channel-link-callback",
        ),
        pytest.param(
            f"/agent/channel-links/callback?code=synthetic-code&state={STATE_VALUE}",
            "/agent/channel-links/callback"
            f"?code={REDACTED_URL_SECRET}&state={REDACTED_URL_SECRET}",
            id="callback-query",
        ),
        pytest.param(
            f"/agent/channel-links/callback?co%64e=synthetic-code&st%61te={STATE_VALUE}",
            "/agent/channel-links/callback"
            f"?co%64e={REDACTED_URL_SECRET}&st%61te={REDACTED_URL_SECRET}",
            id="encoded-query-names",
        ),
        pytest.param(
            f"/agent/channel-links/callback?x='&state={STATE_VALUE}",
            f"/agent/channel-links/callback?x='&state={REDACTED_URL_SECRET}",
            id="quote-before-secret",
        ),
        pytest.param(
            f"/agent/channel-links/callback?x=#&state={STATE_VALUE}",
            f"/agent/channel-links/callback?x=#&state={REDACTED_URL_SECRET}",
            id="hash-before-secret",
        ),
        pytest.param(
            "/cases?postcode=12345", "/cases?postcode=12345", id="query-other"
        ),
        pytest.param(
            "/api/x\n2026 | ERROR forged",
            "/api/x\\x0a2026 | ERROR forged",
            id="newline",
        ),
        pytest.param("/workflows/wf_1/webhook", "/workflows/wf_1/webhook", id="other"),
    ],
)
def test_redact_url_secrets(text: str, expected: str) -> None:
    assert redact_url_secrets(text) == expected


def _route_paths(routes: Sequence[object]) -> Generator[str]:
    """Yield the full path template of every route, including routers.

    FastAPI includes routers lazily: ``app.routes`` holds one
    ``_IncludedRouter`` per ``include_router`` call, whose resolved routes are
    only reachable through ``effective_candidates()``.
    """
    for route in routes:
        if isinstance(route, _IncludedRouter):
            for candidate in route.effective_candidates():
                if isinstance(candidate, _IncludedRouter):
                    yield from _route_paths([candidate])
                else:
                    yield candidate.path
        elif isinstance(route, APIRoute):
            yield route.path


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param(
            "page=2&token=synthetic-token",
            f"page=2&token={REDACTED_URL_SECRET}",
            id="token",
        ),
        pytest.param(
            "id_token=synthetic&x=1&refresh%5Ftoken=synthetic",
            f"id_token={REDACTED_URL_SECRET}&x=1&refresh%5Ftoken={REDACTED_URL_SECRET}",
            id="encoded-name",
        ),
        pytest.param("page=2&postcode=1&code", "page=2&postcode=1&code", id="other"),
    ],
)
def test_redact_query_string(query: str, expected: str) -> None:
    assert redact_query_string(query) == expected


def test_every_secret_route_is_redacted() -> None:
    secret_routes = [
        (template, names)
        for template in _route_paths(app.routes)
        if SECRET_PATH_PARAMS & set(names := _PATH_PARAM.findall(template))
    ]
    assert len(secret_routes) >= 5, "expected the known secret-bearing routes"
    for template, names in secret_routes:
        path = _PATH_PARAM.sub(r"synthetic-\1-value", template)
        redacted = redact_url_secrets(f"/api{path}")
        for name in names:
            value = f"synthetic-{name}-value"
            if name in SECRET_PATH_PARAMS:
                assert value not in redacted, (template, redacted)
            else:
                assert value in redacted, (template, redacted)


@pytest.fixture
def client() -> Generator[TestClient]:
    # No lifespan: the routes under test fail before they touch a database.
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        pytest.param(
            IngestError(IngestErrorCode.SOURCE_NOT_FOUND, "Not found"),
            404,
            id="unknown-token",
        ),
        pytest.param(
            IngestError(IngestErrorCode.MALFORMED, "Not an SNS message"),
            400,
            id="malformed",
        ),
        pytest.param(
            # A failed lookup echoes its bound parameters in the error text.
            RuntimeError(f"connection lost [parameters: ('{INGEST_TOKEN}',)]"),
            500,
            id="unexpected-echoes-token",
        ),
    ],
)
def test_ingest_request_logs_omit_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    async def fail(**_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(sns_router, "handle_sns_message", fail)
    with _captured_loguru() as lines:
        response = client.post(INGEST_PATH, content=b"{}")
    assert response.status_code == expected_status
    _assert_no_secret(lines)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        pytest.param(HTTPException(404, "Not found"), 404, id="unknown-webhook"),
        pytest.param(HTTPException(401, "Unauthorized"), 401, id="wrong-secret"),
        pytest.param(
            RuntimeError(f"lookup failed for {WEBHOOK_SECRET}"),
            500,
            id="unexpected-echoes-secret",
        ),
    ],
)
def test_webhook_request_logs_omit_secret(
    client: TestClient, error: Exception, expected_status: int
) -> None:
    async def fail() -> None:
        raise error

    app.dependency_overrides[validate_incoming_webhook] = fail
    with _captured_loguru() as lines:
        response = client.post(WEBHOOK_PATH, json={})
    assert response.status_code == expected_status
    _assert_no_secret(lines)


def test_unmatched_secret_path_logs_omit_secret(client: TestClient) -> None:
    with _captured_loguru() as lines:
        response = client.post(f"{INGEST_PATH}/extra")
    assert response.status_code == 404
    _assert_no_secret(lines)
    assert any(REDACTED_URL_SECRET in line for line in lines)


@contextlib.contextmanager
def _no_signal_capture(_server: uvicorn.Server) -> Generator[None]:
    yield


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.mark.anyio
async def test_uvicorn_access_log_omits_url_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def lookup_fails(**_kwargs: object) -> None:
        # A failed lookup echoes its bound parameters in the error text.
        raise RuntimeError(f"connection lost [parameters: ('{INGEST_TOKEN}',)]")

    async def unauthorized() -> None:
        raise HTTPException(401, "Unauthorized")

    monkeypatch.setattr(sns_router, "handle_sns_message", lookup_fails)
    monkeypatch.setattr(uvicorn.Server, "capture_signals", _no_signal_capture)
    app.dependency_overrides[validate_incoming_webhook] = unauthorized
    # uvicorn.Config runs logging.config.dictConfig after the app is imported;
    # the filter installed by create_app must survive it.
    uv_server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="info")
    )
    handler = _ListHandler()
    for name in UVICORN_LOGGER_NAMES:
        logging.getLogger(name).addHandler(handler)
    serve_task = asyncio.create_task(uv_server.serve())
    try:
        async with asyncio.timeout(10):
            while not uv_server.started:
                await asyncio.sleep(0.01)
        port = uv_server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=10
        ) as http:
            assert (await http.post(INGEST_PATH, content=b"{}")).status_code == 500
            assert (await http.post(WEBHOOK_PATH, json={})).status_code == 401
            response = await http.get(f"/unknown?st%61te={STATE_VALUE}")
            assert response.status_code == 404
    finally:
        uv_server.should_exit = True
        await serve_task
        for name in UVICORN_LOGGER_NAMES:
            logging.getLogger(name).removeHandler(handler)
        app.dependency_overrides.clear()

    access_lines = [line for line in handler.lines if "HTTP/1.1" in line]
    assert len(access_lines) == 3, handler.lines
    _assert_no_secret(handler.lines)
    assert all(REDACTED_URL_SECRET in line for line in access_lines)


def test_secret_url_errors_do_not_escape_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def lookup_fails(**_kwargs: object) -> None:
        raise RuntimeError(f"connection lost [parameters: ('{INGEST_TOKEN}',)]")

    monkeypatch.setattr(sns_router, "handle_sns_message", lookup_fails)
    # raise_server_exceptions=True fails the test if the error is re-raised
    # to the server, where uvicorn and OTel would record its text.
    with _captured_loguru() as lines:
        response = TestClient(app).post(
            INGEST_PATH, content=b"{}", headers={"Origin": "https://example.com"}
        )
    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }
    _assert_no_secret(lines)
    assert any("lookup_fails" in line for line in lines), "expected stack frames"
    # The 500 still passes through the outer layers (CORS here; the security
    # headers sit beside it outside development).
    assert "access-control-allow-origin" in response.headers


async def _synthetic_callback(code: str, state: str) -> None:
    raise RuntimeError(f"lookup failed for {state}")


async def _synthetic_plain_route() -> None:
    raise RuntimeError("plain failure")


class _SyntheticCallbackQuery(BaseModel):
    code: str
    state: str


async def _synthetic_model_callback(
    callback: Annotated[_SyntheticCallbackQuery, Query()],
) -> None:
    raise RuntimeError(f"lookup failed for {callback.state}")


app.add_api_route("/synthetic/callback", _synthetic_callback)
app.add_api_route("/synthetic/model-callback", _synthetic_model_callback)
app.add_api_route("/synthetic/plain", _synthetic_plain_route)


async def _synthetic_item_route(item_id: str) -> None:
    raise RuntimeError("item failure")


async def _synthetic_file_route(file_path: str) -> None:
    raise RuntimeError("file failure")


app.add_api_route("/synthetic/items/{item_id}", _synthetic_item_route)
app.add_api_route("/synthetic/files/{file_path:path}", _synthetic_file_route)


@pytest.mark.parametrize("route", ["/synthetic/callback", "/synthetic/model-callback"])
def test_errors_on_routes_with_credential_query_params_do_not_escape(
    route: str,
) -> None:
    with _captured_loguru() as lines:
        response = TestClient(app).get(
            f"{route}?code=synthetic-code&state={STATE_VALUE}"
        )
    assert response.status_code == 500
    _assert_no_secret(lines)


def test_other_errors_still_reach_the_server_error_layer() -> None:
    # A credential-shaped query name on a route that declares none does not
    # change how its error is handled.
    with pytest.raises(RuntimeError, match="plain failure"):
        TestClient(app).get(f"/synthetic/plain?token={STATE_VALUE}")


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/synthetic/items/x%00y", id="control-character"),
        pytest.param("/synthetic/files/ingest/sns/x", id="secret-shaped-text"),
    ],
)
def test_request_text_does_not_select_secret_error_handling(path: str) -> None:
    # Only the matched route decides; text that looks like a secret URL on a
    # route without secret parameters keeps the normal error path.
    with pytest.raises(RuntimeError, match="failure"):
        TestClient(app).get(path)


def test_filter_redacts_mapping_args() -> None:
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        __file__,
        1,
        "%(path)s",
        ({"path": f"/ingest/sns/{INGEST_TOKEN}"},),
        None,
    )
    URLSecretRedactionFilter().filter(record)
    assert record.getMessage() == f"/ingest/sns/{REDACTED_URL_SECRET}"


def test_filter_redacts_traceback_text() -> None:
    try:
        raise ValueError(f"bad URL /ingest/sns/{INGEST_TOKEN}")
    except ValueError:
        record = logging.LogRecord(
            "uvicorn.error", logging.ERROR, __file__, 1, "boom", None, sys.exc_info()
        )
    URLSecretRedactionFilter().filter(record)
    formatted = logging.Formatter().format(record)
    assert INGEST_TOKEN not in formatted
    assert f"/ingest/sns/{REDACTED_URL_SECRET}" in formatted
    assert "\n" in formatted, "tracebacks keep their line breaks"


def test_uvicorn_filter_installed_once() -> None:
    install_uvicorn_url_secret_redaction()
    install_uvicorn_url_secret_redaction()
    for name in UVICORN_LOGGER_NAMES:
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, URLSecretRedactionFilter) for f in filters) == 1, name
