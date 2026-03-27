from __future__ import annotations

from typing import cast

import httpx
import orjson
import pytest

from tracecat.agent.llm_proxy.provider_anthropic import (
    AnthropicAdapter,
    anthropic_request_to_openai_payload,
    anthropic_tools_to_openai_tools,
    create_tool_name_mapping,
    restore_tool_name,
    tool_choice_to_anthropic,
    tool_choice_to_openai,
    tool_definition_to_openai,
    tool_result_content_to_openai,
    tool_result_to_anthropic_block,
    truncate_tool_name,
)
from tracecat.agent.llm_proxy.provider_azure_ai import (
    AzureAIAdapter,
    AzureFoundryErrorStrings,
    drop_extra_params_from_request_data,
    extract_params_to_drop_from_error_text,
    should_retry_llm_api_inside_llm_translation_on_http_error,
    transform_request_on_unprocessable_entity_error,
)
from tracecat.agent.llm_proxy.types import (
    IngressFormat,
    NormalizedMessage,
    NormalizedMessagesRequest,
    ProviderHTTPRequest,
)


def _make_anthropic_request(
    *,
    tools: tuple[dict[str, object], ...] = (),
    tool_choice: object = None,
    base_url: str | None = None,
) -> NormalizedMessagesRequest:
    return NormalizedMessagesRequest(
        provider="anthropic",
        model="claude-3-5-sonnet",
        messages=(
            NormalizedMessage(role="system", content="system instructions"),
            NormalizedMessage(role="user", content="hello"),
        ),
        output_format=IngressFormat.ANTHROPIC,
        stream=True,
        base_url=base_url,
        tools=tools,
        tool_choice=tool_choice,
        model_settings={},
    )


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        status_code: int,
        lines: list[str] | None = None,
        body: bytes = b"",
        payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self._lines = lines or []
        self._body = body
        self._payload = payload or {}

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return self._body

    def json(self) -> dict[str, object]:
        return self._payload

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="ignore")


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        json: dict[str, object] | None = None,
    ) -> _FakeStreamContext:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
                "json": json,
            }
        )
        return _FakeStreamContext(self.response)


def test_tool_name_truncation_and_mapping_round_trip() -> None:
    name = "tool-" + "x" * 100
    truncated = truncate_tool_name(name)

    assert len(truncated) <= 64
    assert truncated != name
    assert truncated == truncate_tool_name(name)

    openai_tools, mapping = anthropic_tools_to_openai_tools([{"name": name}])
    assert openai_tools[0]["function"]["name"] == truncated
    assert create_tool_name_mapping([{"name": name}])[truncated] == name
    assert mapping[truncated] == name
    assert restore_tool_name(truncated, mapping) == name
    assert restore_tool_name("short_name", mapping) == "short_name"


def test_tool_definition_and_choice_helpers() -> None:
    anthropic_tool = {
        "name": "lookup_customer",
        "description": "Look up a customer",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
        },
    }
    openai_tool = tool_definition_to_openai(anthropic_tool)
    assert openai_tool == {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": "Look up a customer",
            "parameters": anthropic_tool["input_schema"],
        },
    }
    tool_choice = {"name": "lookup_customer"}
    assert tool_choice_to_openai(tool_choice) == {
        "type": "function",
        "function": {"name": "lookup_customer"},
    }
    assert tool_choice_to_anthropic({"function": {"name": "lookup_customer"}}) == {
        "type": "tool",
        "name": "lookup_customer",
    }


def test_tool_result_helpers() -> None:
    assert tool_result_content_to_openai("ok") == "ok"
    assert tool_result_content_to_openai({"type": "text", "text": "ok"}) == "ok"
    assert tool_result_content_to_openai(["ok"]) == "ok"
    assert tool_result_to_anthropic_block(
        tool_use_id="tool_1",
        content="ok",
        is_error=True,
    ) == {
        "type": "tool_result",
        "tool_use_id": "tool_1",
        "content": "ok",
        "is_error": True,
    }


def test_anthropic_request_to_openai_payload_truncates_tool_names() -> None:
    long_name = "tool-" + "x" * 100
    request = _make_anthropic_request(
        tools=(
            {
                "name": long_name,
                "description": "Long tool",
                "input_schema": {"type": "object"},
            },
        ),
        tool_choice={"name": long_name},
    )

    payload, mapping = anthropic_request_to_openai_payload(request)

    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": truncate_tool_name(long_name)},
    }
    assert payload["tools"][0]["function"]["name"] == truncate_tool_name(long_name)
    assert mapping[payload["tools"][0]["function"]["name"]] == long_name


@pytest.mark.anyio
async def test_anthropic_adapter_prepare_request_uses_anthropic_headers() -> None:
    request = _make_anthropic_request(base_url="https://api.anthropic.com")
    outbound = AnthropicAdapter().prepare_request(
        request,
        {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "ANTHROPIC_VERSION": "2024-01-01",
            "ANTHROPIC_BETA": "beta-1",
        },
    )

    assert outbound.method == "POST"
    assert outbound.url == "https://api.anthropic.com/v1/messages"
    assert outbound.headers["x-api-key"] == "anthropic-key"
    assert outbound.headers["anthropic-version"] == "2024-01-01"
    assert outbound.headers["anthropic-beta"] == "beta-1"
    assert outbound.json_body is not None
    assert outbound.json_body["stream"] is True


@pytest.mark.anyio
async def test_anthropic_adapter_streams_incrementally() -> None:
    lines = [
        "event: message_start",
        f"data: {orjson.dumps({'type': 'message_start', 'message': {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-3-5-sonnet', 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 3, 'output_tokens': 0}}}).decode()}",
        "",
        "event: content_block_start",
        f"data: {orjson.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}).decode()}",
        "",
        "event: content_block_delta",
        f"data: {orjson.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Hello'}}).decode()}",
        "",
        "event: content_block_stop",
        f"data: {orjson.dumps({'type': 'content_block_stop', 'index': 0}).decode()}",
        "",
        "event: message_delta",
        f"data: {orjson.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 4}}).decode()}",
        "",
        "event: message_stop",
        f"data: {orjson.dumps({'type': 'message_stop'}).decode()}",
        "",
        "data: [DONE]",
        "",
    ]
    response = _FakeStreamResponse(status_code=200, lines=lines)
    client = _FakeAsyncClient(response)
    request = _make_anthropic_request()

    events = [
        event
        async for event in AnthropicAdapter().stream_anthropic(
            cast(httpx.AsyncClient, client),
            request,
            {"ANTHROPIC_API_KEY": "anthropic-key"},
        )
    ]

    assert [event.event for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[2].payload["delta"]["text"] == "Hello"
    assert client.calls[0]["url"] == "https://api.anthropic.com/v1/messages"


def test_azure_ai_prepare_request_uses_x_api_key_and_anthropic_url() -> None:
    request = _make_anthropic_request(base_url="https://ignored.example")
    outbound = AzureAIAdapter().prepare_request(
        request,
        {
            "AZURE_API_BASE": "https://resource.services.ai.azure.com",
            "AZURE_API_KEY": "azure-key",
            "AZURE_AI_MODEL_NAME": "claude-3-5-sonnet",
            "ANTHROPIC_VERSION": "2024-01-01",
        },
    )

    assert (
        outbound.url == "https://resource.services.ai.azure.com/anthropic/v1/messages"
    )
    assert outbound.headers["x-api-key"] == "azure-key"
    assert "api-key" not in outbound.headers
    assert outbound.headers["anthropic-version"] == "2024-01-01"


def test_azure_ai_retry_helpers_handle_known_errors() -> None:
    assert should_retry_llm_api_inside_llm_translation_on_http_error(
        "Extra inputs are not permitted",
        should_drop_params=True,
    )
    assert should_retry_llm_api_inside_llm_translation_on_http_error(
        "unknown field: parameter index is not a valid field",
        should_drop_params=False,
    )
    assert should_retry_llm_api_inside_llm_translation_on_http_error(
        AzureFoundryErrorStrings.SET_EXTRA_PARAMETERS_TO_PASS_THROUGH.value,
        should_drop_params=False,
    )
    assert not should_retry_llm_api_inside_llm_translation_on_http_error(
        "some other error",
        should_drop_params=False,
    )


def test_azure_ai_request_transformers_remove_bad_fields() -> None:
    assert extract_params_to_drop_from_error_text(
        "Extra parameters ['stream_options', 'extra-parameters'] are not allowed"
    ) == ["stream_options", "extra-parameters"]

    request_data = {
        "stream_options": {"include_usage": True},
        "extra_body": {"trace": True},
        "extra-parameters": "pass-through",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 4,
                        "id": "tool_1",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            }
        ],
    }

    dropped = drop_extra_params_from_request_data(
        request_data,
        AzureFoundryErrorStrings.SET_EXTRA_PARAMETERS_TO_PASS_THROUGH.value,
    )
    assert "stream_options" not in dropped
    assert "extra_body" not in dropped
    assert "extra-parameters" not in dropped

    transformed = transform_request_on_unprocessable_entity_error(
        request_data,
        "unknown field: parameter index is not a valid field",
    )
    assert "index" not in transformed["messages"][0]["tool_calls"][0]


@pytest.mark.anyio
async def test_azure_ai_prepare_retry_request_rewrites_request_shape() -> None:
    request = _make_anthropic_request()
    outbound = ProviderHTTPRequest(
        method="POST",
        url="https://resource.services.ai.azure.com/anthropic/v1/messages",
        headers={"Content-Type": "application/json", "x-api-key": "azure-key"},
        json_body={
            "stream_options": {"include_usage": True},
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 4,
                            "id": "tool_1",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                }
            ],
        },
        stream=True,
    )
    response = httpx.Response(
        422,
        request=httpx.Request("POST", outbound.url),
        content=b"unknown field: parameter index is not a valid field",
    )

    retried = AzureAIAdapter().prepare_retry_request(
        response=response,
        request=request,
        credentials={
            "AZURE_API_BASE": "https://resource.services.ai.azure.com",
            "AZURE_API_KEY": "azure-key",
            "AZURE_AI_MODEL_NAME": "claude-3-5-sonnet",
        },
        outbound_request=outbound,
        attempt=1,
    )

    assert retried is not None
    assert retried.json_body is not None
    assert "stream_options" in retried.json_body
    assert "index" not in retried.json_body["messages"][0]["tool_calls"][0]
