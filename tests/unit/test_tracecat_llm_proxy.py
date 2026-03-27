from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

import httpx
import orjson
import pytest

from tracecat.agent.llm_proxy.auth import (
    _TRACE_REQUEST_ID_HEADER,
    get_trace_request_id,
    verify_claims_from_headers,
)
from tracecat.agent.llm_proxy.core import TracecatLLMProxy
from tracecat.agent.llm_proxy.credentials import (
    AgentManagementCredentialResolver,
)
from tracecat.agent.llm_proxy.providers import (
    AnthropicAdapter,
    AzureAIAdapter,
    BedrockAdapter,
    GeminiAdapter,
    OpenAICompatibleAdapter,
    VertexAIAdapter,
)
from tracecat.agent.llm_proxy.requests import (
    anthropic_stream_events_from_response,
    filter_allowed_model_settings,
    messages_request_to_anthropic_payload,
    messages_request_to_openai_payload,
    normalize_anthropic_request,
    render_anthropic_response,
    render_anthropic_stream_event,
    stream_anthropic_response,
    truncate_tool_call_id,
)
from tracecat.agent.llm_proxy.types import (
    AnthropicStreamEvent,
    IngressFormat,
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedResponse,
    NormalizedToolCall,
    ProviderHTTPRequest,
)
from tracecat.agent.tokens import LLMTokenClaims


@dataclass(slots=True)
class _FakeResponse:
    status_code: int = 200
    payload: dict[str, object] | None = None
    text: str = "ok"

    def json(self) -> dict[str, object]:
        return self.payload or {}


class _FakeStreamingResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        lines: list[str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return self._body


class _FakeStreamingClient:
    def __init__(self, response: _FakeStreamingResponse) -> None:
        self._response = response

    @asynccontextmanager
    async def stream(self, *args: object, **kwargs: object):
        del args, kwargs
        yield self._response


@pytest.mark.anyio
async def test_verify_claims_from_headers_uses_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="openai",
        base_url="https://example.invalid",
        model_settings={"temperature": 0.2},
        use_workspace_credentials=True,
    )

    def fake_verify(token: str) -> LLMTokenClaims:
        assert token == "llm-token"
        return expected_claims

    monkeypatch.setattr("tracecat.agent.llm_proxy.auth.verify_llm_token", fake_verify)
    headers = {
        "authorization": "Bearer llm-token",
        _TRACE_REQUEST_ID_HEADER: "trace-1",
    }

    claims = verify_claims_from_headers(headers)
    assert claims is expected_claims
    assert get_trace_request_id(headers) == "trace-1"


def test_tool_call_ids_are_deterministically_truncated() -> None:
    tool_id = "tool-call-" + "x" * 64
    first = truncate_tool_call_id(tool_id)
    second = truncate_tool_call_id(tool_id)

    assert first == second
    assert len(first) <= 40
    assert first != tool_id


def test_normalize_anthropic_request_includes_system_message() -> None:
    payload = {
        "model": "claude-sonnet-4-5-20250929",
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    normalized = normalize_anthropic_request(
        payload,
        provider="anthropic",
        model="claude-sonnet-4-5-20250929",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    assert normalized.messages[0].role == "system"
    assert normalized.messages[0].content == "You are a helpful assistant."
    rendered = render_anthropic_response(
        NormalizedResponse(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            content="hi",
            tool_calls=(),
            finish_reason="end_turn",
            usage={"input_tokens": 3, "output_tokens": 5},
            raw={"id": "msg-1"},
        )
    )
    assert rendered["content"][0]["text"] == "hi"


def test_normalize_anthropic_request_translates_tool_result_blocks() -> None:
    payload = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Calling tool"},
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "lookup",
                        "input": {"query": "status"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "ok",
                    }
                ],
            },
        ],
    }

    normalized = normalize_anthropic_request(
        payload,
        provider="openai",
        model="gpt-5-mini",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    assert normalized.messages[0].role == "assistant"
    assert normalized.messages[0].tool_calls[0].id == "toolu_123"
    assert normalized.messages[1].role == "tool"
    assert normalized.messages[1].tool_call_id == "toolu_123"
    assert normalized.messages[1].content == "ok"


def test_anthropic_tool_result_text_block_renders_as_openai_tool_string() -> None:
    payload = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "lookup",
                        "input": {"query": "status"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ],
            },
        ],
    }

    normalized = normalize_anthropic_request(
        payload,
        provider="openai",
        model="gpt-5-mini",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )
    rendered = messages_request_to_openai_payload(normalized)

    assert rendered["messages"][1]["role"] == "tool"
    assert rendered["messages"][1]["content"] == "ok"


def test_anthropic_tool_result_precedes_same_turn_user_text_in_openai_payload() -> None:
    payload = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "lookup",
                        "input": {"query": "status"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": {"status": "ok"},
                    },
                    {"type": "text", "text": "Continue."},
                ],
            },
        ],
    }

    normalized = normalize_anthropic_request(
        payload,
        provider="openai",
        model="gpt-5-mini",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )
    rendered = messages_request_to_openai_payload(normalized)

    assert [message["role"] for message in rendered["messages"]] == [
        "assistant",
        "tool",
        "user",
    ]
    assert rendered["messages"][1]["content"] == '{"status":"ok"}'
    assert rendered["messages"][2]["content"] == "Continue."


def test_anthropic_multi_text_blocks_render_as_openai_content_parts() -> None:
    payload = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "First."},
                    {"type": "text", "text": "Second."},
                ],
            }
        ],
    }

    normalized = normalize_anthropic_request(
        payload,
        provider="openai",
        model="gpt-5-mini",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )
    rendered = messages_request_to_openai_payload(normalized)

    assert rendered["messages"][0]["content"] == [
        {"type": "text", "text": "First."},
        {"type": "text", "text": "Second."},
    ]


def test_filter_allowed_model_settings_discards_unknown_keys() -> None:
    assert filter_allowed_model_settings(
        {
            "temperature": 0.1,
            "api_key": "secret",
            "seed": 42,
            "max_completion_tokens": 128,
        }
    ) == {"temperature": 0.1, "seed": 42, "max_completion_tokens": 128}


def test_anthropic_tools_render_as_openai_function_tools() -> None:
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5-mini",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        tools=(
            {
                "name": "lookup_status",
                "description": "Look up status",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ),
    )

    payload = messages_request_to_openai_payload(request)

    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup_status",
                "description": "Look up status",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]


def test_openai_tools_render_as_anthropic_tools() -> None:
    request = NormalizedMessagesRequest(
        provider="anthropic",
        model="claude-sonnet-4-5-20250929",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "lookup_status",
                    "description": "Look up status",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
        ),
    )

    payload = messages_request_to_anthropic_payload(request)

    assert payload["tools"] == [
        {
            "name": "lookup_status",
            "description": "Look up status",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]


def test_stream_anthropic_response_emits_message_stop() -> None:
    events = stream_anthropic_response(
        NormalizedResponse(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            content="hello",
            finish_reason="end_turn",
            usage={"input_tokens": 3, "output_tokens": 5},
            raw={"id": "msg-1"},
        )
    )

    assert events[0].startswith(b"event: message_start\n")
    assert events[-1] == b'event: message_stop\ndata: {"type":"message_stop"}\n\n'


def test_render_anthropic_stream_event_matches_sse_shape() -> None:
    rendered = render_anthropic_stream_event(
        AnthropicStreamEvent(
            "message_stop",
            {"type": "message_stop"},
        )
    )

    assert rendered == b'event: message_stop\ndata: {"type":"message_stop"}\n\n'


def test_anthropic_stream_events_from_response_builds_tool_use_events() -> None:
    events = anthropic_stream_events_from_response(
        NormalizedResponse(
            provider="openai",
            model="gpt-5-mini",
            content="hello",
            tool_calls=(
                NormalizedToolCall(
                    id="call_1",
                    name="lookup",
                    arguments={"query": "status"},
                ),
            ),
            finish_reason="tool_use",
            usage={"input_tokens": 3, "output_tokens": 5},
            raw={"id": "msg-1"},
        )
    )

    assert [event.event for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]


def test_provider_adapters_build_expected_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5-mini",
        messages=(
            NormalizedMessage(role="system", content="system"),
            NormalizedMessage(
                role="user",
                content="hello",
                tool_calls=(
                    NormalizedToolCall(
                        id="tool-call-" + "x" * 50,
                        name="lookup",
                        arguments={"query": "status"},
                    ),
                ),
            ),
        ),
        output_format=IngressFormat.ANTHROPIC,
        stream=False,
        base_url="https://proxy.example",
        api_version="2024-02-15-preview",
        tools=(),
        model_settings={"temperature": 0.1},
    )

    openai_request = OpenAICompatibleAdapter("openai").prepare_request(
        request,
        {"OPENAI_API_KEY": "sk-test"},
    )
    assert openai_request.json_body is not None
    assert openai_request.url.endswith("/v1/chat/completions")
    assert openai_request.headers["Authorization"] == "Bearer sk-test"
    assert (
        openai_request.json_body["messages"][1]["tool_calls"][0]["function"][
            "arguments"
        ]
        == orjson.dumps({"query": "status"}).decode()
    )

    custom_request = OpenAICompatibleAdapter("custom-model-provider").prepare_request(
        request,
        {
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://custom.example",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "custom-chat",
        },
    )
    assert custom_request.json_body is not None
    assert custom_request.headers.get("Authorization") is None
    assert custom_request.json_body["model"] == "custom-chat"
    assert custom_request.url == "https://custom.example/v1/chat/completions"

    azure_request = OpenAICompatibleAdapter("azure_openai").prepare_request(
        request,
        {
            "AZURE_API_BASE": "https://azure.example",
            "AZURE_API_VERSION": "2024-02-15-preview",
            "AZURE_DEPLOYMENT_NAME": "deployment",
            "AZURE_API_KEY": "azure-key",
        },
    )
    assert "api-version=2024-02-15-preview" in azure_request.url
    assert azure_request.headers["api-key"] == "azure-key"

    anthropic_request = AnthropicAdapter().prepare_request(
        request,
        {"ANTHROPIC_API_KEY": "anth-key"},
    )
    assert anthropic_request.url.endswith("/v1/messages")
    assert anthropic_request.headers["x-api-key"] == "anth-key"

    gemini_request = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )
    assert gemini_request.json_body is not None
    assert gemini_request.url.endswith(":generateContent")
    assert gemini_request.headers["x-goog-api-key"] == "gem-key"
    assert gemini_request.json_body["contents"][0]["role"] == "user"

    class _FakeServiceAccountCredentials:
        token = "vertex-token"

        def refresh(self, _: object) -> None:
            return None

    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        lambda *args, **kwargs: _FakeServiceAccountCredentials(),
    )
    vertex_request = VertexAIAdapter().prepare_request(
        request,
        {
            "GOOGLE_API_CREDENTIALS": "{}",
            "GOOGLE_CLOUD_PROJECT": "tracecat",
            "VERTEX_AI_MODEL": "gemini-2.5-pro",
        },
    )
    assert vertex_request.url.endswith(
        "/publishers/google/models/gemini-2.5-pro:generateContent"
    )
    assert vertex_request.headers["Authorization"] == "Bearer vertex-token"

    bedrock_request = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
        },
    )
    assert bedrock_request.url.endswith(
        "/model/anthropic.claude-3-haiku-20240307-v1%3A0/converse"
    )
    assert bedrock_request.headers["Authorization"] == "Bearer bedrock-token"

    azure_ai_request = AzureAIAdapter().prepare_request(
        request,
        {
            "AZURE_API_BASE": "https://azure-ai.example/anthropic",
            "AZURE_API_KEY": "ai-key",
            "AZURE_AI_MODEL_NAME": "claude-sonnet-4-5",
        },
    )
    assert azure_ai_request.url.endswith("/v1/messages")
    assert azure_ai_request.headers["x-api-key"] == "ai-key"
    assert azure_ai_request.headers["anthropic-version"] == "2023-06-01"


def test_openai_compatible_adapter_uses_max_completion_tokens_for_gpt5_models() -> None:
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5.2",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=False,
        model_settings={"max_tokens": 128, "temperature": 0.1},
    )

    openai_request = OpenAICompatibleAdapter("openai").prepare_request(
        request,
        {"OPENAI_API_KEY": "sk-test"},
    )

    assert openai_request.json_body is not None
    assert openai_request.json_body["max_completion_tokens"] == 128
    assert "max_tokens" not in openai_request.json_body


def test_openai_compatible_adapter_prefers_explicit_max_completion_tokens() -> None:
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5.2",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=False,
        model_settings={"max_tokens": 128, "max_completion_tokens": 64},
    )

    openai_request = OpenAICompatibleAdapter("openai").prepare_request(
        request,
        {"OPENAI_API_KEY": "sk-test"},
    )

    assert openai_request.json_body is not None
    assert openai_request.json_body["max_completion_tokens"] == 64
    assert "max_tokens" not in openai_request.json_body


def test_openai_compatible_adapter_keeps_supported_gpt_5_2_temperature() -> None:
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5.2",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=False,
        model_settings={"temperature": 0.2, "reasoning_effort": "none"},
    )

    openai_request = OpenAICompatibleAdapter("openai").prepare_request(
        request,
        {"OPENAI_API_KEY": "sk-test"},
    )

    assert openai_request.json_body is not None
    assert openai_request.json_body["temperature"] == 0.2
    assert openai_request.json_body["reasoning_effort"] == "none"


def test_openai_compatible_adapter_drops_unsupported_gpt_5_params() -> None:
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=False,
        model_settings={
            "temperature": 0.2,
            "top_p": 0.8,
            "presence_penalty": 0.1,
            "frequency_penalty": 0.1,
            "stop": ["done"],
            "reasoning_effort": "xhigh",
        },
    )

    openai_request = OpenAICompatibleAdapter("openai").prepare_request(
        request,
        {"OPENAI_API_KEY": "sk-test"},
    )

    assert openai_request.json_body is not None
    assert "temperature" not in openai_request.json_body
    assert "top_p" not in openai_request.json_body
    assert "presence_penalty" not in openai_request.json_body
    assert "frequency_penalty" not in openai_request.json_body
    assert "stop" not in openai_request.json_body
    assert "reasoning_effort" not in openai_request.json_body


@pytest.mark.anyio
async def test_openai_streaming_adapter_translates_text_deltas() -> None:
    adapter = OpenAICompatibleAdapter("openai")
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5-mini",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=True,
    )
    client = cast(
        httpx.AsyncClient,
        _FakeStreamingClient(
            _FakeStreamingResponse(
                lines=[
                    'data: {"id":"chatcmpl-1","model":"gpt-5-mini","choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"},"finish_reason":null}]}',
                    "",
                    'data: {"id":"chatcmpl-1","model":"gpt-5-mini","choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}]}',
                    "",
                    "data: [DONE]",
                    "",
                ]
            )
        ),
    )

    events = [
        event
        async for event in adapter.stream_anthropic(
            client,
            request,
            {"OPENAI_API_KEY": "sk-test"},
        )
    ]

    assert [event.event for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[2].payload["delta"]["text"] == "Hel"
    assert events[3].payload["delta"]["text"] == "lo"
    assert events[5].payload["delta"]["stop_reason"] == "end_turn"


@pytest.mark.anyio
async def test_openai_streaming_adapter_translates_tool_calls() -> None:
    adapter = OpenAICompatibleAdapter("openai")
    request = NormalizedMessagesRequest(
        provider="openai",
        model="gpt-5-mini",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=True,
    )
    client = cast(
        httpx.AsyncClient,
        _FakeStreamingClient(
            _FakeStreamingResponse(
                lines=[
                    'data: {"id":"chatcmpl-1","model":"gpt-5-mini","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\\"query\\":"}}]},"finish_reason":null}]}',
                    "",
                    'data: {"id":"chatcmpl-1","model":"gpt-5-mini","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"status\\"}"}}]},"finish_reason":"tool_calls"}]}',
                    "",
                    "data: [DONE]",
                    "",
                ]
            )
        ),
    )

    events = [
        event
        async for event in adapter.stream_anthropic(
            client,
            request,
            {"OPENAI_API_KEY": "sk-test"},
        )
    ]

    assert [event.event for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[1].payload["content_block"]["type"] == "tool_use"
    assert events[2].payload["delta"]["partial_json"] == '{"query":'
    assert events[3].payload["delta"]["partial_json"] == '"status"}'
    assert events[5].payload["delta"]["stop_reason"] == "tool_use"


@pytest.mark.anyio
async def test_anthropic_streaming_adapter_passes_through_events() -> None:
    adapter = AnthropicAdapter()
    request = NormalizedMessagesRequest(
        provider="anthropic",
        model="claude-sonnet-4-5-20250929",
        messages=(NormalizedMessage(role="user", content="hello"),),
        output_format=IngressFormat.ANTHROPIC,
        stream=True,
    )
    client = cast(
        httpx.AsyncClient,
        _FakeStreamingClient(
            _FakeStreamingResponse(
                lines=[
                    "event: message_start",
                    'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-sonnet-4-5-20250929","usage":{"input_tokens":3,"output_tokens":0}}}',
                    "",
                    "event: content_block_delta",
                    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}',
                    "",
                    "event: message_stop",
                    'data: {"type":"message_stop"}',
                    "",
                ]
            )
        ),
    )

    events = [
        event
        async for event in adapter.stream_anthropic(
            client,
            request,
            {"ANTHROPIC_API_KEY": "anth-key"},
        )
    ]

    assert [event.event for event in events] == [
        "message_start",
        "content_block_delta",
        "message_stop",
    ]


@pytest.mark.anyio
async def test_proxy_tracks_success_and_error_counts(
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

    class _Adapter:
        provider = "openai"

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            assert credentials["OPENAI_API_KEY"] == "sk-test"
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
            return NormalizedResponse(
                provider="openai",
                model=request.model,
                content="ok",
                raw=response.json(),
            )

    proxy.provider_registry.adapters["openai"] = _Adapter()

    async def _request(*args: object, **kwargs: object) -> httpx.Response:
        del args, kwargs
        return httpx.Response(200, json={"id": "chatcmpl-1"})

    monkeypatch.setattr(proxy.http_client, "request", _request)
    event_stream = await proxy.stream_messages(
        payload={"messages": [{"role": "user", "content": "hello"}]},
        claims=claims,
    )
    chunks = [chunk async for chunk in event_stream]
    assert any(b"ok" in chunk for chunk in chunks)
    assert proxy.state.active_requests == 0
    assert proxy.state.total_requests == 1
    assert proxy.state.total_errors == 0

    proxy._track_start()
    proxy._track_end(error=True)

    assert proxy.state.active_requests == 0
    assert proxy.state.total_requests == 2
    assert proxy.state.total_errors == 1


@pytest.mark.anyio
async def test_stream_messages_tracks_active_requests_until_stream_completion(
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
    stream_started: list[int] = []

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
            )

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del response, request
            raise AssertionError("streaming path should not call parse_response")

        async def stream_anthropic(
            self,
            client: httpx.AsyncClient,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ):
            del client, request, credentials
            stream_started.append(proxy.state.active_requests)
            yield AnthropicStreamEvent(
                "message_start",
                {"type": "message_start", "message": {"id": "msg-1"}},
            )
            yield AnthropicStreamEvent("message_stop", {"type": "message_stop"})

    proxy.provider_registry.adapters["openai"] = _StreamingAdapter()

    events = await proxy.stream_messages(
        payload={"messages": [{"role": "user", "content": "hello"}], "stream": True},
        claims=claims,
    )

    assert proxy.state.active_requests == 1
    rendered = [chunk async for chunk in events]

    assert rendered[0].startswith(b"event: message_start\n")
    assert rendered[-1] == b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    assert stream_started == [1]
    assert proxy.state.active_requests == 0
    assert proxy.state.total_requests == 1
    assert proxy.state.total_errors == 0


@pytest.mark.anyio
async def test_proxy_falls_back_to_buffered_streaming_for_non_streaming_adapters(
    monkeypatch: pytest.MonkeyPatch,
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gemini-2.5-pro",
        provider="gemini",
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory({"GEMINI_API_KEY": "gem-key"})

    async def fake_execute_request(
        self: TracecatLLMProxy,
        *,
        request: NormalizedMessagesRequest,
        adapter: object,
        credentials: dict[str, str],
    ) -> NormalizedResponse:
        del self, request, adapter, credentials
        return NormalizedResponse(
            provider="gemini",
            model="gemini-2.5-pro",
            content="ok",
            raw={"id": "msg-1"},
        )

    monkeypatch.setattr(TracecatLLMProxy, "_execute_request", fake_execute_request)

    events = await proxy.stream_messages(
        payload={"messages": [{"role": "user", "content": "hello"}], "stream": True},
        claims=claims,
    )

    rendered = [chunk async for chunk in events]

    assert rendered[0].startswith(b"event: message_start\n")
    assert rendered[-1] == b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    assert proxy.state.active_requests == 0
    assert proxy.state.total_requests == 1
    assert proxy.state.total_errors == 0


@pytest.mark.anyio
async def test_proxy_uses_token_model_settings_and_restricts_base_url(
    monkeypatch: pytest.MonkeyPatch,
    static_llm_proxy_factory,
) -> None:
    claims = LLMTokenClaims(
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
        model="gpt-5-mini",
        provider="gemini",
        base_url="https://should-not-be-used.invalid",
        model_settings={"temperature": 0.3, "api_key": "nope"},
        use_workspace_credentials=False,
    )
    proxy = static_llm_proxy_factory({"GEMINI_API_KEY": "gem-key"})
    captured: dict[str, object] = {}

    class _Adapter:
        provider = "gemini"

        def prepare_request(
            self,
            request: NormalizedMessagesRequest,
            credentials: dict[str, str],
        ) -> ProviderHTTPRequest:
            del credentials
            captured["model"] = request.model
            captured["base_url"] = request.base_url
            captured["model_settings"] = request.model_settings
            return ProviderHTTPRequest(
                method="POST",
                url="https://gemini.invalid",
                headers={},
                json_body={},
            )

        async def parse_response(
            self,
            response: httpx.Response,
            request: NormalizedMessagesRequest,
        ) -> NormalizedResponse:
            del response
            return NormalizedResponse(provider="gemini", model=request.model, raw={})

    proxy.provider_registry.adapters["gemini"] = _Adapter()

    async def _request(*args: object, **kwargs: object) -> httpx.Response:
        del args, kwargs
        return httpx.Response(200, json={})

    monkeypatch.setattr(proxy.http_client, "request", _request)

    event_stream = await proxy.stream_messages(
        payload={
            "model": "user-overrode-model",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.1,
        },
        claims=claims,
    )
    async for _ in event_stream:
        pass

    assert captured == {
        "model": "gpt-5-mini",
        "base_url": None,
        "model_settings": {"temperature": 0.3},
    }


def test_build_proxy_uses_agent_management_resolver() -> None:
    proxy = TracecatLLMProxy.build()
    assert isinstance(proxy.credential_resolver, AgentManagementCredentialResolver)
