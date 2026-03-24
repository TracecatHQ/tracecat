from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import httpx
import pytest

from tracecat.agent.litellm_observability import LiteLLMLoadTracker
from tracecat.agent.sandbox.llm_proxy import LLMSocketProxy


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        return None


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        reason_phrase: str = "Error",
        body: bytes = b"{}",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.reason_phrase = reason_phrase
        self.headers = {"content-type": "application/json"}
        self._body = body
        self._chunks = chunks or []

    async def aread(self) -> bytes:
        return self._body

    async def aiter_bytes(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


class _FakeClient:
    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc

    @asynccontextmanager
    async def stream(self, **_: object):
        if self._exc is not None:
            raise self._exc
        if self._response is None:
            raise RuntimeError("Missing fake response")
        yield self._response


@pytest.mark.anyio
async def test_forward_request_returns_explicit_http_error_response_for_529(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tracker = LiteLLMLoadTracker()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy._proxy_load_tracker", tracker)
    errors: list[str] = []
    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        on_error=errors.append,
    )
    proxy._client = cast(
        httpx.AsyncClient,
        _FakeClient(
            response=_FakeResponse(status_code=529, reason_phrase="Overloaded"),
        ),
    )
    writer = _FakeWriter()

    await proxy._forward_request(
        {
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": {"Content-Type": "application/json"},
            "body": b"{}",
        },
        cast(asyncio.StreamWriter, writer),
    )

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 529")
    assert "LLM provider is overloaded - please try again shortly" in response_text
    assert errors == ["LLM provider is overloaded - please try again shortly"]
    assert tracker.snapshot().active_requests == 0


@pytest.mark.anyio
async def test_forward_request_maps_timeout_to_gateway_timeout_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tracker = LiteLLMLoadTracker()
    monkeypatch.setattr("tracecat.agent.sandbox.llm_proxy._proxy_load_tracker", tracker)
    errors: list[str] = []
    proxy = LLMSocketProxy(
        socket_path=tmp_path / "llm.sock",
        on_error=errors.append,
    )
    proxy._client = cast(
        httpx.AsyncClient,
        _FakeClient(exc=httpx.ReadTimeout("timed out")),
    )
    writer = _FakeWriter()

    await proxy._forward_request(
        {
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": {"Content-Type": "application/json"},
            "body": b"{}",
        },
        cast(asyncio.StreamWriter, writer),
    )

    response_text = writer.buffer.decode("utf-8")
    assert response_text.startswith("HTTP/1.1 504")
    assert "Gateway timeout" in response_text
    assert errors == ["Gateway timeout"]
    assert tracker.snapshot().active_requests == 0
