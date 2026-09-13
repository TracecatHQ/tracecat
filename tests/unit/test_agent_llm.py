from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from tracecat.agent import llm as llm_module
from tracecat.agent.llm import _call_litellm, _call_passthrough
from tracecat.network import DisallowedUrlError


def _completion_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={"choices": [{"message": {"content": "done"}}]},
    )


@pytest.mark.anyio
async def test_passthrough_completion_uses_guarded_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _completion_response(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    factory = Mock(return_value=client)
    monkeypatch.setattr(llm_module, "guarded_async_client", factory)
    try:
        result = await _call_passthrough(
            messages=[{"role": "user", "content": "hello"}],
            model_name="model-a",
            base_url="https://provider.example/v1",
            api_key="secret",
            max_tokens=32,
            timeout_seconds=3.0,
        )
    finally:
        if not client.is_closed:
            await client.aclose()

    assert result == "done"
    factory.assert_called_once()
    assert str(requests[0].url) == "https://provider.example/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.anyio
async def test_passthrough_completion_blocks_private_target() -> None:
    with pytest.raises(DisallowedUrlError, match="Host is not allowed"):
        await _call_passthrough(
            messages=[{"role": "user", "content": "hello"}],
            model_name="model-a",
            base_url="http://127.0.0.1:1/v1",
            api_key="secret",
            max_tokens=None,
            timeout_seconds=1.0,
        )


@pytest.mark.anyio
async def test_managed_completion_preserves_internal_litellm_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _completion_response(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client_factory = Mock(return_value=client)
    guarded_factory = Mock(side_effect=AssertionError("managed route was guarded"))
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(llm_module, "guarded_async_client", guarded_factory)
    monkeypatch.setattr(llm_module, "TRACECAT__LITELLM_BASE_URL", "http://litellm:4000")
    try:
        result = await _call_litellm(
            messages=[{"role": "user", "content": "hello"}],
            token="llm-token",
            max_tokens=None,
            timeout_seconds=3.0,
        )
    finally:
        if not client.is_closed:
            await client.aclose()

    assert result == "done"
    client_factory.assert_called_once_with(timeout=3.0)
    guarded_factory.assert_not_called()
    assert str(requests[0].url) == "http://litellm:4000/chat/completions"
