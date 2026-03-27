from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from tracecat.agent.llm_proxy.providers import ProviderRetryAdapter
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    IngressFormat,
    NormalizedMessagesRequest,
    NormalizedResponse,
    ProviderHTTPRequest,
)
from tracecat.agent.tokens import LLMTokenClaims


@pytest.mark.anyio
async def test_proxy_uses_token_parallel_tool_calls_and_response_format(
    monkeypatch: pytest.MonkeyPatch,
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="openai",
        model_settings={
            "parallel_tool_calls": False,
            "response_format": {"type": "json_object"},
            "verbosity": "low",
        },
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory({"OPENAI_API_KEY": "sk-test"})
    captured: dict[str, object] = {}

    class _Adapter:
        provider = "openai"

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            del credentials
            captured["parallel_tool_calls"] = request.parallel_tool_calls
            captured["response_format"] = request.response_format
            captured["model_settings"] = request.model_settings
            return ProviderHTTPRequest(
                method="POST",
                url="https://proxy.example/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json_body={},
            )

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del response
            return NormalizedResponse(
                provider="openai",
                model=request.model,
                content="ok",
                raw={},
            )

    proxy.provider_registry.adapters["openai"] = _Adapter()

    async def _request(*args: object, **kwargs: object) -> httpx.Response:
        del args, kwargs
        return httpx.Response(200, json={})

    monkeypatch.setattr(proxy.http_client, "request", _request)

    event_stream = await proxy.stream_messages(
        payload={
            "messages": [{"role": "user", "content": "hello"}],
        },
        claims=claims,
    )
    async for _ in event_stream:
        pass

    assert captured == {
        "parallel_tool_calls": False,
        "response_format": {"type": "json_object"},
        "model_settings": {"verbosity": "low"},
    }


@pytest.mark.anyio
async def test_proxy_retries_once_with_provider_mutation(
    monkeypatch: pytest.MonkeyPatch,
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="openai",
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory({"OPENAI_API_KEY": "sk-test"})
    requests_seen: list[dict[str, object]] = []

    class _Adapter(ProviderRetryAdapter):
        provider = "openai"

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            del request, credentials
            return ProviderHTTPRequest(
                method="POST",
                url="https://proxy.example/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json_body={"messages": [], "bad_field": True},
            )

        def prepare_retry_request(
            self,
            *,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
            outbound_request: ProviderHTTPRequest,
            attempt: int,
        ) -> ProviderHTTPRequest | None:
            del request, credentials
            assert response.status_code == 422
            assert attempt == 0
            assert outbound_request.json_body is not None
            retry_body = dict(outbound_request.json_body)
            retry_body.pop("bad_field", None)
            return ProviderHTTPRequest(
                method=outbound_request.method,
                url=outbound_request.url,
                headers=outbound_request.headers,
                json_body=retry_body,
            )

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del request
            if response.status_code >= 400:
                raise RuntimeError(f"openai provider error: {response.status_code}")
            return NormalizedResponse(
                provider="openai",
                model="gpt-5-mini",
                content="ok",
                raw=response.json(),
            )

    proxy.provider_registry.adapters["openai"] = _Adapter()

    async def _request(*args: object, **kwargs: object) -> httpx.Response:
        requests_seen.append(dict(kwargs))
        if len(requests_seen) == 1:
            return httpx.Response(422, json={"error": "bad field"})
        return httpx.Response(200, json={"id": "chatcmpl-1"})

    monkeypatch.setattr(proxy.http_client, "request", _request)

    event_stream = await proxy.stream_messages(
        payload={"messages": [{"role": "user", "content": "hello"}]},
        claims=claims,
    )
    chunks = [chunk async for chunk in event_stream]
    assert any(b"ok" in chunk for chunk in chunks)
    assert len(requests_seen) == 2
    assert requests_seen[0]["json"] == {"messages": [], "bad_field": True}
    assert requests_seen[1]["json"] == {"messages": []}


@pytest.mark.anyio
async def test_proxy_preserves_provider_specific_payload_settings_for_bedrock(
    monkeypatch: pytest.MonkeyPatch,
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="anthropic.claude-3-7-sonnet",
        provider="bedrock",
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory(
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        }
    )
    captured: dict[str, object] = {}

    class _Adapter:
        provider = "bedrock"

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            del credentials
            captured["model_settings"] = request.model_settings
            return ProviderHTTPRequest(
                method="POST",
                url="https://bedrock.invalid/converse",
                headers={"Content-Type": "application/json"},
                body=b"{}",
            )

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del response
            return NormalizedResponse(provider="bedrock", model=request.model, raw={})

    proxy.provider_registry.adapters["bedrock"] = _Adapter()

    async def _request(*args: object, **kwargs: object) -> httpx.Response:
        del args, kwargs
        return httpx.Response(200, json={})

    monkeypatch.setattr(proxy.http_client, "request", _request)

    event_stream = await proxy.stream_messages(
        payload={
            "messages": [{"role": "user", "content": "hello"}],
            "top_k": 64,
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        },
        claims=claims,
    )
    async for _ in event_stream:
        pass

    assert captured["model_settings"] == {
        "top_k": 64,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
    }


@pytest.mark.anyio
async def test_proxy_passes_authorized_model_to_anthropic_passthrough(
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="claude-sonnet-4-5-20250929",
        provider="anthropic",
        base_url="https://anthropic.example",
        model_settings={"temperature": 0.3},
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory({"ANTHROPIC_API_KEY": "anth-key"})
    captured: dict[str, object] = {}

    class _PassthroughAdapter:
        provider = "anthropic"
        native_format = IngressFormat.ANTHROPIC

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            del request, credentials
            raise AssertionError("passthrough path should not call prepare_request")

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del response, request
            raise AssertionError("passthrough path should not call parse_response")

        async def passthrough_stream(
            self,
            client: httpx.AsyncClient,
            payload: dict[str, object],
            credentials: dict[str, str],
            model_settings: dict[str, object],
            *,
            model: str,
            base_url: str | None = None,
        ):
            del client
            captured["payload_model"] = payload["model"]
            captured["credentials"] = credentials
            captured["model_settings"] = model_settings
            captured["model"] = model
            captured["base_url"] = base_url
            yield b"event: message_stop\ndata: {}\n\n"

    proxy.provider_registry.adapters["anthropic"] = _PassthroughAdapter()
    payload = {
        "model": "user-overrode-model",
        "messages": [{"role": "user", "content": "hello"}],
    }

    events = await proxy.stream_messages(payload=payload, claims=claims)
    rendered = [chunk async for chunk in events]

    assert rendered == [b"event: message_stop\ndata: {}\n\n"]
    assert captured == {
        "payload_model": "user-overrode-model",
        "credentials": {"ANTHROPIC_API_KEY": "anth-key"},
        "model_settings": {"temperature": 0.3},
        "model": "claude-sonnet-4-5-20250929",
        "base_url": "https://anthropic.example",
    }
    assert payload["model"] == "user-overrode-model"


@pytest.mark.anyio
async def test_proxy_buffers_non_stream_requests_for_streaming_adapters(
    monkeypatch: pytest.MonkeyPatch,
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="openai",
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory({"OPENAI_API_KEY": "sk-test"})
    captured: dict[str, object] = {}

    class _StreamingAdapter:
        provider = "openai"

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            del request, credentials
            return ProviderHTTPRequest(
                method="POST",
                url="https://proxy.example/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json_body={"messages": []},
            )

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del response, request
            raise AssertionError(
                "non-stream requests should use the buffered execute path"
            )

        async def stream_anthropic(
            self,
            client: httpx.AsyncClient,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ):
            del client, request, credentials
            raise AssertionError("non-stream requests should not use the live SSE path")
            yield AnthropicStreamEvent("message_stop", {"type": "message_stop"})

    async def fake_execute_request(
        self,
        *,
        request: NormalizedMessagesRequest,
        adapter: object,
        credentials: dict[str, str],
    ) -> NormalizedResponse:
        del self, adapter
        captured["stream"] = request.stream
        captured["credentials"] = credentials
        return NormalizedResponse(
            provider="openai",
            model=request.model,
            content="hello",
            raw={"id": "chatcmpl-1"},
        )

    proxy.provider_registry.adapters["openai"] = _StreamingAdapter()
    monkeypatch.setattr(type(proxy), "_execute_request", fake_execute_request)

    events = await proxy.stream_messages(
        payload={"messages": [{"role": "user", "content": "hello"}]},
        claims=claims,
    )
    rendered = [chunk async for chunk in events]

    assert any(b"hello" in chunk for chunk in rendered)
    assert captured == {
        "stream": False,
        "credentials": {"OPENAI_API_KEY": "sk-test"},
    }
