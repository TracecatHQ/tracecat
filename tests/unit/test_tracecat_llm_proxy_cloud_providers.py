from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import httpx
import orjson
import pytest
from botocore.parsers import EventStreamJSONParser

import tracecat.agent.llm_proxy.provider_bedrock as provider_bedrock
from tracecat.agent.llm_proxy.provider_bedrock import BedrockAdapter
from tracecat.agent.llm_proxy.provider_google import GeminiAdapter, VertexAIAdapter
from tracecat.agent.llm_proxy.requests import normalize_anthropic_request
from tracecat.agent.llm_proxy.types import (
    IngressFormat,
    NormalizedMessage,
    NormalizedMessagesRequest,
    NormalizedToolCall,
    ProviderHTTPRequest,
)
from tracecat.cases.schemas import CaseCreate


@dataclass(slots=True)
class _FakeServiceAccountCredentials:
    token: str = "vertex-token"

    def refresh(self, _: object) -> None:
        return None


class _FakeBedrockStreamResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return b"".join(self._chunks)

    @property
    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="ignore")


class _FakeBedrockStreamContext:
    def __init__(self, response: _FakeBedrockStreamResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeBedrockStreamResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeBedrockStreamingClient:
    def __init__(
        self, response: _FakeBedrockStreamResponse | list[_FakeBedrockStreamResponse]
    ) -> None:
        self.responses = response if isinstance(response, list) else [response]
        self.calls: list[dict[str, object]] = []

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> _FakeBedrockStreamContext:
        response = self.responses[min(len(self.calls), len(self.responses) - 1)]
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "content": content,
            }
        )
        return _FakeBedrockStreamContext(response)


class _FakeRawBedrockEvent:
    def __init__(self, response_dict: dict[str, object]) -> None:
        self._response_dict = response_dict

    def to_response_dict(self) -> dict[str, object]:
        return self._response_dict


class _FakeEventStreamJSONParser(EventStreamJSONParser):
    def __init__(self, parsed_event: dict[str, Any]) -> None:
        super().__init__()
        self._parsed_event = parsed_event

    def parse(self, response: Mapping[str, Any], shape: Any) -> dict[str, Any]:
        del response, shape
        return self._parsed_event


def _base_request(
    *,
    provider: str,
    model: str,
    messages: tuple[NormalizedMessage, ...],
    model_settings: dict[str, object] | None = None,
    tools: tuple[dict[str, object], ...] = (),
    tool_choice: object | None = None,
    response_format: dict[str, object] | None = None,
) -> NormalizedMessagesRequest:
    return NormalizedMessagesRequest(
        provider=provider,
        model=model,
        messages=messages,
        output_format=IngressFormat.ANTHROPIC,
        stream=True,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        model_settings=model_settings or {},
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )


def test_gemini_request_normalizes_system_instruction_tools_and_response_format() -> (
    None
):
    request = _base_request(
        provider="gemini",
        model="gemini-2.5-pro",
        messages=(
            NormalizedMessage(role="system", content="Be concise."),
            NormalizedMessage(
                role="user",
                content=[
                    {"type": "text", "text": "hello"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.invalid/cat.png"},
                    },
                ],
            ),
            NormalizedMessage(
                role="assistant",
                tool_calls=(
                    NormalizedToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "status"},
                    ),
                ),
            ),
            NormalizedMessage(
                role="tool",
                tool_call_id="call_1",
                name="lookup",
                content={"status": "ok"},
            ),
        ),
        model_settings={
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 32,
            "max_tokens": 256,
            "reasoning_effort": "medium",
        },
        tools=(
            {
                "name": "lookup",
                "description": "Lookup a record",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        ),
        tool_choice={"type": "tool", "name": "lookup"},
        response_format={
            "type": "json_schema",
            "json_schema": {
                "schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                }
            },
        },
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.url.endswith(":generateContent")
    assert request_http.headers["x-goog-api-key"] == "gem-key"
    assert request_http.json_body is not None
    assert request_http.json_body["systemInstruction"] == {
        "parts": [{"text": "Be concise."}]
    }
    assert request_http.json_body["contents"][0]["role"] == "user"
    assert request_http.json_body["contents"][1]["role"] == "model"
    assert request_http.json_body["contents"][2]["role"] == "user"
    assert request_http.json_body["contents"][1]["parts"][0]["functionCall"] == {
        "name": "lookup",
        "args": {"query": "status"},
    }
    assert request_http.json_body["contents"][2]["parts"][0]["functionResponse"] == {
        "name": "lookup",
        "response": {"status": "ok"},
    }
    assert request_http.json_body["generationConfig"]["max_output_tokens"] == 256
    assert request_http.json_body["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 2048,
    }
    assert (
        request_http.json_body["generationConfig"]["responseMimeType"]
        == "application/json"
    )
    assert request_http.json_body["generationConfig"]["responseSchema"] == {
        "type": "object",
        "properties": {"status": {"type": "string"}},
    }
    assert request_http.json_body["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["lookup"],
        }
    }
    assert request_http.json_body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "lookup",
                    "description": "Lookup a record",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                }
            ]
        }
    ]


def test_gemini_request_preserves_assistant_text_with_tool_calls() -> None:
    request = _base_request(
        provider="gemini",
        model="gemini-2.5-pro",
        messages=(
            NormalizedMessage(role="user", content="hello"),
            NormalizedMessage(
                role="assistant",
                content="I will look that up.",
                tool_calls=(
                    NormalizedToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "status"},
                    ),
                ),
            ),
        ),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"][1]["parts"] == [
        {"text": "I will look that up."},
        {
            "functionCall": {
                "name": "lookup",
                "args": {"query": "status"},
            }
        },
    ]


def test_gemini_3_request_uses_thinking_level_for_reasoning_effort() -> None:
    request = _base_request(
        provider="gemini",
        model="gemini-3-flash-preview",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={"reasoning_effort": "medium"},
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingLevel": "MEDIUM",
    }


def test_gemini_request_sanitizes_unsupported_schema_keywords() -> None:
    request = _base_request(
        provider="gemini",
        model="gemini-2.5-pro",
        messages=(NormalizedMessage(role="user", content="hello"),),
        tools=(
            {
                "name": "lookup",
                "description": "Lookup a record",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "x-tracecat-component": ["text"],
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "propertyNames": {"pattern": "^[a-z_]+$"},
                                "properties": {
                                    "field": {"type": "string"},
                                    "value": {
                                        "type": "string",
                                        "x-tracecat-component": ["text"],
                                    },
                                },
                                "required": ["field", "value", "missing"],
                            },
                        },
                    },
                    "required": ["query", "missing"],
                    "propertyNames": {"pattern": "^[a-z_]+$"},
                },
            },
        ),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "object",
                            "propertyNames": {"pattern": "^[a-z_]+$"},
                            "properties": {
                                "status": {
                                    "type": "string",
                                    "x-tracecat-component": ["text"],
                                }
                            },
                            "required": ["status", "missing"],
                        }
                    },
                }
            },
        },
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "lookup",
                    "description": "Lookup a record",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "filters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "field": {"type": "string"},
                                        "value": {"type": "string"},
                                    },
                                    "required": ["field", "value"],
                                },
                            },
                        },
                        "required": ["query"],
                    },
                }
            ]
        }
    ]
    assert request_http.json_body["generationConfig"]["responseSchema"] == {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
            }
        },
    }


def test_gemini_request_from_anthropic_payload_preserves_tool_response_name() -> None:
    request = normalize_anthropic_request(
        {
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
                        }
                    ],
                },
            ],
            "tools": (
                {
                    "name": "lookup",
                    "description": "Lookup a record",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            ),
        },
        provider="gemini",
        model="gemini-2.5-pro",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"] == [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": "lookup",
                        "args": {"query": "status"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "lookup",
                        "response": {"status": "ok"},
                    }
                }
            ],
        },
    ]


def test_gemini_request_from_anthropic_payload_splits_tool_results_from_user_text() -> (
    None
):
    request = normalize_anthropic_request(
        {
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
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_456",
                            "name": "lookup",
                            "input": {"query": "summary"},
                        },
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
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_456",
                            "content": {"summary": "ready"},
                        },
                        {"type": "text", "text": "Continue."},
                    ],
                },
            ],
            "tools": (
                {
                    "name": "lookup",
                    "description": "Lookup a record",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            ),
        },
        provider="gemini",
        model="gemini-2.5-pro",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"] == [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": "lookup",
                        "args": {"query": "status"},
                    }
                },
                {
                    "functionCall": {
                        "name": "lookup",
                        "args": {"query": "summary"},
                    }
                },
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "lookup",
                        "response": {"status": "ok"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "lookup",
                        "response": {"summary": "ready"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [{"text": "Continue."}],
        },
    ]


def test_gemini_request_preserves_inline_server_tool_result_ordering() -> None:
    request = normalize_anthropic_request(
        {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me search first."},
                        {
                            "type": "server_tool_use",
                            "id": "stu_ws_001",
                            "name": "web_search",
                            "input": {"query": "tracecat docs"},
                        },
                        {
                            "type": "web_search_tool_result",
                            "tool_use_id": "stu_ws_001",
                            "content": [
                                {
                                    "type": "web_search_result",
                                    "title": "Tracecat Docs",
                                    "url": "https://docs.tracecat.com",
                                    "encrypted_content": "enc_abc",
                                }
                            ],
                        },
                        {"type": "text", "text": "The docs confirm the setup."},
                    ],
                }
            ],
        },
        provider="gemini",
        model="gemini-2.5-pro",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"] == [
        {
            "role": "model",
            "parts": [
                {"text": "Let me search first."},
                {
                    "functionCall": {
                        "name": "web_search",
                        "args": {"query": "tracecat docs"},
                    }
                },
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "web_search",
                        "response": {
                            "content": "- Tracecat Docs\n  https://docs.tracecat.com"
                        },
                    }
                }
            ],
        },
        {
            "role": "model",
            "parts": [{"text": "The docs confirm the setup."}],
        },
    ]


def test_gemini_request_from_anthropic_payload_preserves_mixed_block_order() -> None:
    request = normalize_anthropic_request(
        {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I will look that up."},
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "lookup",
                            "input": {"query": "status"},
                        },
                        {"type": "text", "text": "Then I will summarize it."},
                    ],
                }
            ],
        },
        provider="gemini",
        model="gemini-2.5-pro",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"][0]["parts"] == [
        {"text": "I will look that up."},
        {
            "functionCall": {
                "name": "lookup",
                "args": {"query": "status"},
            }
        },
        {"text": "Then I will summarize it."},
    ]


def test_gemini_request_from_anthropic_payload_attaches_thought_signature_to_tool_use() -> (
    None
):
    request = normalize_anthropic_request(
        {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "I should inspect the record first.",
                            "signature": "sig-thought-1",
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "lookup",
                            "input": {"query": "status"},
                        },
                    ],
                }
            ],
        },
        provider="gemini",
        model="gemini-2.5-pro",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"] == [
        {
            "role": "model",
            "parts": [
                {
                    "text": "I should inspect the record first.",
                    "thought": True,
                },
                {
                    "functionCall": {
                        "name": "lookup",
                        "args": {"query": "status"},
                    },
                    "thoughtSignature": "sig-thought-1",
                },
            ],
        }
    ]


def test_gemini_request_preserves_array_items_after_schema_cleanup() -> None:
    case_create_schema = CaseCreate.model_json_schema()
    request = _base_request(
        provider="gemini",
        model="gemini-2.5-pro",
        messages=(NormalizedMessage(role="user", content="hello"),),
        tools=(
            {
                "name": "create_case",
                "description": "Create a case",
                "input_schema": {
                    "type": "object",
                    "$defs": case_create_schema.get("$defs", {}),
                    "properties": {
                        "dropdown_values": case_create_schema["properties"][
                            "dropdown_values"
                        ],
                        "rich_text": {
                            "type": "array",
                            "items": {
                                "x-tracecat-component": ["rich_text"],
                            },
                        },
                    },
                },
            },
        ),
    )

    request_http = GeminiAdapter().prepare_request(
        request,
        {"GEMINI_API_KEY": "gem-key"},
    )

    assert request_http.json_body is not None
    parameters = request_http.json_body["tools"][0]["functionDeclarations"][0][
        "parameters"
    ]
    dropdown_values = parameters["properties"]["dropdown_values"]
    assert dropdown_values["anyOf"][0]["type"] == "array"
    assert dropdown_values["anyOf"][0]["items"]["type"] == "object"
    assert "properties" in dropdown_values["anyOf"][0]["items"]
    assert parameters["properties"]["rich_text"] == {
        "type": "array",
        "items": {"type": "object"},
    }


@pytest.mark.anyio
async def test_gemini_response_parses_text_reasoning_and_tool_calls() -> None:
    response = httpx.Response(
        200,
        json={
            "model": "gemini-2.5-pro",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "hello"},
                            {
                                "text": "I should check the record first.",
                                "thought": True,
                            },
                            {
                                "functionCall": {
                                    "name": "lookup",
                                    "args": {"query": "status"},
                                },
                                "thoughtSignature": "sig-thought-1",
                            },
                        ]
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 3,
            },
        },
    )

    parsed = await GeminiAdapter().parse_response(
        response,
        _base_request(
            provider="gemini",
            model="gemini-2.5-pro",
            messages=(NormalizedMessage(role="user", content="hello"),),
        ),
    )

    assert parsed.content == [
        {"type": "text", "text": "hello"},
        {
            "type": "thinking",
            "thinking": "I should check the record first.",
            "signature": "sig-thought-1",
        },
    ]
    assert parsed.finish_reason == "STOP"
    assert parsed.usage == {"input_tokens": 7, "output_tokens": 3}
    assert parsed.tool_calls == (
        NormalizedToolCall(
            id="lookup-2",
            name="lookup",
            arguments={"query": "status"},
        ),
    )


def test_vertex_request_uses_system_instruction_and_blank_user_message() -> None:
    request = _base_request(
        provider="vertex_ai",
        model="gemini-2.5-pro",
        messages=(NormalizedMessage(role="system", content="Be concise."),),
        model_settings={"temperature": 0.3, "stop": "END"},
    )

    request_http = VertexAIAdapter().prepare_request(
        request,
        {
            "VERTEX_AI_BEARER_TOKEN": "vertex-token",
            "GOOGLE_CLOUD_PROJECT": "tracecat",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "VERTEX_AI_MODEL": "gemini-2.5-pro",
        },
    )

    assert request_http.url.endswith(
        "/publishers/google/models/gemini-2.5-pro:generateContent"
    )
    assert request_http.headers["Authorization"] == "Bearer vertex-token"
    assert request_http.json_body is not None
    assert request_http.json_body["system_instruction"] == {
        "parts": [{"text": "Be concise."}]
    }
    assert request_http.json_body["contents"] == [
        {"role": "user", "parts": [{"text": " "}]}
    ]
    assert request_http.json_body["generationConfig"]["temperature"] == 0.3
    assert request_http.json_body["generationConfig"]["stop_sequences"] == ["END"]


@pytest.mark.parametrize(
    ("system_content", "expected_text"),
    [
        # Plain string — unchanged
        ("Be concise.", "Be concise."),
        # Anthropic-style block list — extract text
        (
            [
                {"type": "text", "text": "First block."},
                {"type": "text", "text": "Second block."},
            ],
            "First block.\nSecond block.",
        ),
        # Single dict block
        ({"type": "text", "text": "From dict."}, "From dict."),
    ],
    ids=["plain-string", "block-list", "single-dict"],
)
def test_gemini_system_instruction_extracts_text_from_structured_content(
    system_content: Any,
    expected_text: str,
) -> None:
    """System messages with structured content blocks must produce clean text."""
    request = _base_request(
        provider="gemini",
        model="gemini-2.5-pro",
        messages=(
            NormalizedMessage(role="system", content=system_content),
            NormalizedMessage(role="user", content="hi"),
        ),
    )
    request_http = GeminiAdapter().prepare_request(
        request, {"GEMINI_API_KEY": "test-key"}
    )
    assert request_http.json_body is not None
    assert request_http.json_body["systemInstruction"] == {
        "parts": [{"text": expected_text}]
    }


@pytest.mark.parametrize(
    ("location", "expected_base_url"),
    [
        ("europe-west4", "https://europe-west4-aiplatform.googleapis.com"),
        ("global", "https://aiplatform.googleapis.com"),
    ],
)
def test_vertex_request_uses_location_specific_endpoint(
    location: str, expected_base_url: str
) -> None:
    request = _base_request(
        provider="vertex_ai",
        model="gemini-2.5-pro",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )

    request_http = VertexAIAdapter().prepare_request(
        request,
        {
            "VERTEX_AI_BEARER_TOKEN": "vertex-token",
            "GOOGLE_CLOUD_PROJECT": "tracecat",
            "GOOGLE_CLOUD_LOCATION": location,
            "VERTEX_AI_MODEL": "gemini-2.5-pro",
        },
    )

    assert request_http.url == (
        f"{expected_base_url}/v1/projects/tracecat/locations/{location}"
        "/publishers/google/models/gemini-2.5-pro:generateContent"
    )


@pytest.mark.anyio
async def test_vertex_response_parses_text_reasoning_and_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        lambda *args, **kwargs: _FakeServiceAccountCredentials(),
    )
    response = httpx.Response(
        200,
        json={
            "model": "gemini-2.5-pro",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "hello"},
                            {
                                "text": "I should check the record first.",
                                "thought": True,
                            },
                            {
                                "functionCall": {
                                    "name": "lookup",
                                    "args": {"query": "status"},
                                },
                                "thoughtSignature": "sig-thought-1",
                            },
                        ]
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 2,
            },
        },
    )

    parsed = await VertexAIAdapter().parse_response(
        response,
        _base_request(
            provider="vertex_ai",
            model="gemini-2.5-pro",
            messages=(NormalizedMessage(role="user", content="hello"),),
        ),
    )

    assert parsed.content == [
        {"type": "text", "text": "hello"},
        {
            "type": "thinking",
            "thinking": "I should check the record first.",
            "signature": "sig-thought-1",
        },
    ]
    assert parsed.finish_reason == "STOP"
    assert parsed.tool_calls == (
        NormalizedToolCall(
            id="lookup-2",
            name="lookup",
            arguments={"query": "status"},
        ),
    )
    assert parsed.usage == {"input_tokens": 5, "output_tokens": 2}


def test_vertex_request_from_anthropic_payload_attaches_thought_signature_to_tool_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        lambda *args, **kwargs: _FakeServiceAccountCredentials(),
    )
    request = normalize_anthropic_request(
        {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "I should inspect the record first.",
                            "signature": "sig-thought-1",
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "lookup",
                            "input": {"query": "status"},
                        },
                    ],
                }
            ],
        },
        provider="vertex_ai",
        model="gemini-2.5-pro",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = VertexAIAdapter().prepare_request(
        request,
        {
            "VERTEX_AI_BEARER_TOKEN": "vertex-token",
            "GOOGLE_CLOUD_PROJECT": "tracecat",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "VERTEX_AI_MODEL": "gemini-2.5-pro",
        },
    )

    assert request_http.json_body is not None
    assert request_http.json_body["contents"] == [
        {
            "role": "model",
            "parts": [
                {
                    "text": "I should inspect the record first.",
                    "thought": True,
                },
                {
                    "functionCall": {
                        "name": "lookup",
                        "args": {"query": "status"},
                    },
                    "thoughtSignature": "sig-thought-1",
                },
            ],
        }
    ]


def test_bedrock_request_normalizes_system_tools_and_inference_config() -> None:
    request = _base_request(
        provider="bedrock",
        model="amazon.nova-2-lite-v1:0",
        messages=(
            NormalizedMessage(role="system", content="Be concise."),
            NormalizedMessage(role="user", content="hello"),
            NormalizedMessage(
                role="assistant",
                tool_calls=(
                    NormalizedToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "status"},
                    ),
                ),
            ),
            NormalizedMessage(
                role="tool",
                tool_call_id="call_1",
                name="lookup",
                content="ok",
            ),
        ),
        model_settings={
            "max_tokens": 128,
            "temperature": 0.2,
            "top_p": 0.9,
            "stop": ["END"],
            "reasoning_effort": "high",
        },
        tools=(
            {
                "name": "lookup",
                "description": "Lookup a record",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        ),
        tool_choice={"type": "tool", "name": "lookup"},
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "amazon.nova-2-lite-v1:0",
        },
    )

    assert request_http.url.endswith("/model/amazon.nova-2-lite-v1%3A0/converse")
    assert request_http.headers["Authorization"] == "Bearer bedrock-token"
    assert request_http.body is not None

    payload = orjson.loads(request_http.body)
    assert "modelId" not in payload
    assert payload["system"] == [{"text": "Be concise."}]
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][1]["role"] == "assistant"
    assert payload["messages"][2]["role"] == "user"
    assert payload["messages"][1]["content"][0]["toolUse"] == {
        "toolUseId": "call_1",
        "name": "lookup",
        "input": {"query": "status"},
    }
    assert payload["messages"][2]["content"][0]["toolResult"] == {
        "toolUseId": "call_1",
        "content": [{"text": "ok"}],
    }
    assert payload["inferenceConfig"] == {
        "maxTokens": 128,
        "temperature": 0.2,
        "topP": 0.9,
        "stopSequences": ["END"],
    }
    assert payload["additionalModelRequestFields"] == {
        "reasoningConfig": {
            "type": "enabled",
            "maxReasoningEffort": "high",
        }
    }
    assert payload["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "lookup",
                    "description": "Lookup a record",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        }
                    },
                }
            }
        ],
        "toolChoice": {"tool": {"name": "lookup"}},
    }


def test_bedrock_request_assumes_role_and_uses_inference_profile_id(mocker) -> None:
    request = _base_request(
        provider="bedrock",
        model="bedrock",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )
    sts_client = mocker.MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "assumed-access",
            "SecretAccessKey": "assumed-secret",
            "SessionToken": "assumed-token",
        }
    }
    # AWS role assumption now happens in credential resolver, not prepare_request
    # Pass pre-assumed credentials (as would come from resolved credentials)
    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": sts_client.assume_role.return_value["Credentials"][
                "AccessKeyId"
            ],
            "AWS_SECRET_ACCESS_KEY": sts_client.assume_role.return_value["Credentials"][
                "SecretAccessKey"
            ],
            "AWS_SESSION_TOKEN": sts_client.assume_role.return_value["Credentials"][
                "SessionToken"
            ],
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        },
    )
    assert request_http.url.endswith(
        "/model/us.anthropic.claude-sonnet-4-20250514-v1%3A0/converse"
    )
    assert request_http.headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=assumed-access/"
    )
    assert request_http.headers["X-Amz-Security-Token"] == "assumed-token"


def test_bedrock_request_uses_resolved_claude_target_for_reasoning_translation() -> (
    None
):
    request = _base_request(
        provider="bedrock",
        model="bedrock",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={"reasoning_effort": "medium"},
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["additionalModelRequestFields"] == {
        "thinking": {
            "type": "enabled",
            "budget_tokens": 2048,
        }
    }


def test_bedrock_request_preserves_assistant_text_with_tool_calls() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        messages=(
            NormalizedMessage(role="user", content="hello"),
            NormalizedMessage(
                role="assistant",
                content="I will look that up.",
                tool_calls=(
                    NormalizedToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "status"},
                    ),
                ),
            ),
        ),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["messages"][1]["content"] == [
        {"text": "I will look that up."},
        {
            "toolUse": {
                "toolUseId": "call_1",
                "name": "lookup",
                "input": {"query": "status"},
            }
        },
    ]


def test_bedrock_request_from_anthropic_payload_preserves_mixed_block_order() -> None:
    request = normalize_anthropic_request(
        {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I will look that up."},
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "lookup",
                            "input": {"query": "status"},
                        },
                        {"type": "text", "text": "Then I will summarize it."},
                    ],
                }
            ],
        },
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        session_id=uuid4(),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["messages"][0]["content"] == [
        {"text": "I will look that up."},
        {
            "toolUse": {
                "toolUseId": "toolu_123",
                "name": "lookup",
                "input": {"query": "status"},
            }
        },
        {"text": "Then I will summarize it."},
    ]


def test_bedrock_request_maps_anthropic_thinking_blocks_to_reasoning_content() -> None:
    request = normalize_anthropic_request(
        {
            "model": "claude-sonnet-4-5-20250929",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "I should inspect the record first.",
                            "signature": "sig_123",
                        },
                        {"type": "text", "text": "I will look that up."},
                    ],
                }
            ],
            "stream": True,
        },
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["messages"][0]["content"] == [
        {
            "reasoningContent": {
                "text": "I should inspect the record first.",
                "signature": "sig_123",
            }
        },
        {"text": "I will look that up."},
    ]


def test_bedrock_request_uses_data_uri_payload_for_inline_images() -> None:
    request = _base_request(
        provider="bedrock",
        model="amazon.nova-2-lite-v1:0",
        messages=(
            NormalizedMessage(
                role="user",
                content=[
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,aGVsbG8=",
                            "format": "image/png",
                        },
                    }
                ],
            ),
        ),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "amazon.nova-2-lite-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    image_block = payload["messages"][0]["content"][0]["image"]

    assert image_block == {
        "format": "png",
        "source": {"bytes": "aGVsbG8="},
    }


@pytest.mark.anyio
async def test_bedrock_response_parses_text_and_tool_calls() -> None:
    response = httpx.Response(
        200,
        json={
            "modelId": "amazon.nova-2-lite-v1:0",
            "stopReason": "tool_use",
            "usage": {"inputTokens": 11, "outputTokens": 4},
            "output": {
                "message": {
                    "content": [
                        {"text": "hello"},
                        {
                            "toolUse": {
                                "toolUseId": "call_1",
                                "name": "lookup",
                                "input": {"query": "status"},
                            }
                        },
                    ]
                }
            },
        },
    )

    parsed = await BedrockAdapter().parse_response(
        response,
        _base_request(
            provider="bedrock",
            model="amazon.nova-2-lite-v1:0",
            messages=(NormalizedMessage(role="user", content="hello"),),
        ),
    )

    assert parsed.content == "hello"
    assert parsed.finish_reason == "tool_use"
    assert parsed.usage == {"input_tokens": 11, "output_tokens": 4}
    assert parsed.tool_calls == (
        NormalizedToolCall(
            id="call_1",
            name="lookup",
            arguments={"query": "status"},
        ),
    )


def test_bedrock_request_drops_thinking_for_forced_tool_choice() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={"reasoning_effort": "high"},
        tools=(
            {
                "name": "lookup",
                "input_schema": {"type": "object"},
            },
        ),
        tool_choice={"type": "tool", "name": "lookup"},
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["toolConfig"]["toolChoice"] == {"tool": {"name": "lookup"}}
    assert "additionalModelRequestFields" not in payload


def test_bedrock_request_keeps_auto_tool_choice_when_reasoning_enabled() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={"reasoning_effort": "medium"},
        tools=(
            {
                "name": "lookup",
                "input_schema": {"type": "object"},
            },
        ),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-haiku-4-5-20251001-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "lookup",
                    "inputSchema": {"json": {"type": "object"}},
                }
            }
        ]
    }
    assert payload["additionalModelRequestFields"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 2048,
    }


def test_bedrock_request_preserves_direct_thinking_and_top_k_settings() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={
            "top_k": 24,
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        },
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)

    assert payload["inferenceConfig"]["topK"] == 24
    assert payload["additionalModelRequestFields"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 2048,
    }


def test_bedrock_request_adds_dummy_tool_config_for_tool_history() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=(
            NormalizedMessage(role="user", content="hello"),
            NormalizedMessage(
                role="assistant",
                tool_calls=(
                    NormalizedToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "status"},
                    ),
                ),
            ),
            NormalizedMessage(
                role="tool",
                tool_call_id="call_1",
                name="lookup",
                content="ok",
            ),
        ),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-haiku-4-5-20251001-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "dummy_tool",
                    "description": "This is a dummy tool call",
                    "inputSchema": {"json": {"type": "object", "properties": {}}},
                }
            }
        ]
    }


def test_bedrock_request_groups_parallel_tool_results_into_one_user_turn() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=(
            NormalizedMessage(role="user", content="hello"),
            NormalizedMessage(
                role="assistant",
                tool_calls=(
                    NormalizedToolCall(
                        id="call_1",
                        name="lookup_a",
                        arguments={"query": "alpha"},
                    ),
                    NormalizedToolCall(
                        id="call_2",
                        name="lookup_b",
                        arguments={"query": "beta"},
                    ),
                ),
            ),
            NormalizedMessage(
                role="tool",
                tool_call_id="call_1",
                name="lookup_a",
                content={"status": "ok"},
            ),
            NormalizedMessage(
                role="tool",
                tool_call_id="call_2",
                name="lookup_b",
                content={"status": "ok"},
            ),
        ),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-haiku-4-5-20251001-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["messages"][1]["role"] == "assistant"
    assert payload["messages"][2]["role"] == "user"
    assert len(payload["messages"]) == 3
    assert payload["messages"][2]["content"] == [
        {
            "toolResult": {
                "toolUseId": "call_1",
                "content": [{"json": {"status": "ok"}}],
            }
        },
        {
            "toolResult": {
                "toolUseId": "call_2",
                "content": [{"json": {"status": "ok"}}],
            }
        },
    ]


def test_bedrock_request_accepts_anthropic_tool_shape() -> None:
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=(NormalizedMessage(role="user", content="Use the lookup tool."),),
        tools=(
            {
                "name": "lookup",
                "description": "Lookup a record",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ),
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-haiku-4-5-20251001-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    assert payload["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "lookup",
                    "description": "Lookup a record",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        }
                    },
                }
            }
        ]
    }


def test_bedrock_request_truncates_long_tool_names_and_ids() -> None:
    long_name = "mcp__tracecat-registry__mcp__Notion_MCP__notion-update-data-source"
    long_tool_call_id = "toolu_" + ("abcdef1234567890" * 4)
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=(
            NormalizedMessage(role="user", content="Use the long tool."),
            NormalizedMessage(
                role="assistant",
                tool_calls=(
                    NormalizedToolCall(
                        id=long_tool_call_id,
                        name=long_name,
                        arguments={"query": "status"},
                    ),
                ),
            ),
            NormalizedMessage(
                role="tool",
                tool_call_id=long_tool_call_id,
                name=long_name,
                content={"status": "ok"},
            ),
        ),
        tools=(
            {
                "name": long_name,
                "description": "Update a data source",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        ),
        tool_choice={"type": "tool", "name": long_name},
    )

    request_http = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-haiku-4-5-20251001-v1:0",
        },
    )

    assert request_http.body is not None
    payload = orjson.loads(request_http.body)
    tool_spec_name = payload["toolConfig"]["tools"][0]["toolSpec"]["name"]
    tool_choice_name = payload["toolConfig"]["toolChoice"]["tool"]["name"]
    tool_use = payload["messages"][1]["content"][0]["toolUse"]
    tool_result = payload["messages"][2]["content"][0]["toolResult"]

    assert len(tool_spec_name) <= 64
    assert tool_choice_name == tool_spec_name
    assert tool_use["name"] == tool_spec_name
    assert len(tool_use["toolUseId"]) <= 40
    assert tool_result["toolUseId"] == tool_use["toolUseId"]


@pytest.mark.anyio
async def test_bedrock_prepare_retry_request_drops_thinking_on_tool_use_reasoning_error() -> (
    None
):
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )
    outbound = BedrockAdapter().prepare_request(
        request,
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
    )
    assert outbound.body is not None

    payload = orjson.loads(outbound.body)
    payload["additionalModelRequestFields"] = {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }
    response = httpx.Response(
        400,
        request=httpx.Request("POST", outbound.url),
        content=(
            b"ValidationException: Expected thinking or redacted_thinking, but found tool_use"
        ),
    )

    retried = BedrockAdapter().prepare_retry_request(
        response=response,
        request=request,
        credentials={
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
        outbound_request=provider_bedrock.ProviderHTTPRequest(
            method=outbound.method,
            url=outbound.url,
            headers=outbound.headers,
            body=orjson.dumps(payload),
            stream=outbound.stream,
        ),
    )

    assert retried is not None
    assert retried.body is not None
    retried_payload = orjson.loads(retried.body)
    assert "additionalModelRequestFields" not in retried_payload


def test_bedrock_prepare_retry_request_resigns_assumed_role_requests(mocker) -> None:
    request = _base_request(
        provider="bedrock",
        model="bedrock",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )
    sts_client = mocker.MagicMock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "assumed-access",
            "SecretAccessKey": "assumed-secret",
            "SessionToken": "assumed-token",
        }
    }
    # AWS role assumption now happens in credential resolver, not prepare_retry_request
    outbound = ProviderHTTPRequest(
        method="POST",
        url="https://bedrock.invalid/model/bedrock/converse",
        headers={"Authorization": "stale", "Content-Type": "application/json"},
        body=orjson.dumps(
            {
                "messages": [{"role": "user", "content": [{"text": "hello"}]}],
                "additionalModelRequestFields": {
                    "thinking": {"type": "enabled", "budget_tokens": 2048}
                },
            }
        ),
        stream=False,
    )
    response = httpx.Response(
        400,
        request=httpx.Request("POST", outbound.url),
        content=(
            b"ValidationException: Expected thinking or redacted_thinking, but found tool_use"
        ),
    )

    retried = BedrockAdapter().prepare_retry_request(
        response=response,
        request=request,
        credentials={
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": sts_client.assume_role.return_value["Credentials"][
                "AccessKeyId"
            ],
            "AWS_SECRET_ACCESS_KEY": sts_client.assume_role.return_value["Credentials"][
                "SecretAccessKey"
            ],
            "AWS_SESSION_TOKEN": sts_client.assume_role.return_value["Credentials"][
                "SessionToken"
            ],
            "AWS_MODEL_ID": "bedrock",
        },
        outbound_request=outbound,
    )

    assert retried is not None
    assert retried.body is not None
    assert retried.headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=assumed-access/"
    )
    assert retried.headers["Authorization"] != "stale"
    assert retried.headers["X-Amz-Security-Token"] == "assumed-token"
    retried_payload = orjson.loads(retried.body)
    assert "additionalModelRequestFields" not in retried_payload


@pytest.mark.anyio
async def test_bedrock_prepare_retry_request_drops_thinking_on_forced_tool_choice_error() -> (
    None
):
    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )
    outbound = ProviderHTTPRequest(
        method="POST",
        url="https://bedrock.invalid/model/anthropic.claude-3-7-sonnet/converse",
        headers={"Content-Type": "application/json"},
        body=orjson.dumps(
            {
                "messages": [{"role": "user", "content": [{"text": "hello"}]}],
                "additionalModelRequestFields": {
                    "thinking": {"type": "enabled", "budget_tokens": 2048}
                },
                "toolConfig": {"toolChoice": {"tool": {"name": "lookup"}}},
            }
        ),
        stream=False,
    )
    response = httpx.Response(
        400,
        request=httpx.Request("POST", outbound.url),
        content=(
            b'{"message":"The model returned the following errors: '
            b'Thinking may not be enabled when tool_choice forces tool use."}'
        ),
    )

    retried = BedrockAdapter().prepare_retry_request(
        response=response,
        request=request,
        credentials={
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
        },
        outbound_request=outbound,
    )

    assert retried is not None
    assert retried.body is not None
    retried_payload = orjson.loads(retried.body)
    assert "additionalModelRequestFields" not in retried_payload
    assert retried_payload["toolConfig"]["toolChoice"] == {"tool": {"name": "lookup"}}


@pytest.mark.anyio
async def test_bedrock_prepare_retry_request_drops_reasoning_effort_on_validation_error() -> (
    None
):
    request = _base_request(
        provider="bedrock",
        model="bedrock",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )
    outbound = ProviderHTTPRequest(
        method="POST",
        url="https://bedrock.invalid/model/bedrock/converse",
        headers={"Content-Type": "application/json"},
        body=orjson.dumps(
            {
                "messages": [{"role": "user", "content": [{"text": "hello"}]}],
                "additionalModelRequestFields": {"reasoning_effort": "medium"},
            }
        ),
        stream=False,
    )
    response = httpx.Response(
        400,
        request=httpx.Request("POST", outbound.url),
        content=(
            b'{"message":"The model returned the following errors: '
            b'reasoning_effort: Extra inputs are not permitted"}'
        ),
    )

    retried = BedrockAdapter().prepare_retry_request(
        response=response,
        request=request,
        credentials={
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "bedrock",
        },
        outbound_request=outbound,
    )

    assert retried is not None
    assert retried.body is not None
    retried_payload = orjson.loads(retried.body)
    assert "additionalModelRequestFields" not in retried_payload


@pytest.mark.anyio
async def test_bedrock_streaming_adapter_retries_reasoning_effort_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_iter_events(_: httpx.Response):
        for event in (
            {"messageStart": {"conversationId": "conv-1"}},
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "ok"}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"outputTokens": 3}}},
        ):
            yield event

    def _fake_request_components(
        _: NormalizedMessagesRequest,
        __: dict[str, str],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        assert stream is True
        return {
            "url": "https://bedrock.invalid/model/bedrock/converse-stream",
            "body": orjson.dumps(
                {
                    "messages": [{"role": "user", "content": [{"text": "hello"}]}],
                    "additionalModelRequestFields": {"reasoning_effort": "medium"},
                }
            ),
        }

    def _fake_signed_request(
        *,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        url: str,
        body: bytes,
    ) -> ProviderHTTPRequest:
        del request, credentials
        return ProviderHTTPRequest(
            method="POST",
            url=url,
            headers={"Content-Type": "application/json"},
            body=body,
            stream=True,
        )

    monkeypatch.setattr(
        provider_bedrock, "_iter_bedrock_stream_events", _fake_iter_events
    )
    monkeypatch.setattr(
        provider_bedrock, "_bedrock_request_components", _fake_request_components
    )
    monkeypatch.setattr(
        provider_bedrock, "_signed_bedrock_request", _fake_signed_request
    )

    request = _base_request(
        provider="bedrock",
        model="bedrock",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={"reasoning_effort": "medium"},
    )
    client = _FakeBedrockStreamingClient(
        [
            _FakeBedrockStreamResponse(
                400,
                headers={"content-type": "application/json"},
                chunks=[
                    b'{"message":"The model returned the following errors: '
                    b'reasoning_effort: Extra inputs are not permitted"}'
                ],
            ),
            _FakeBedrockStreamResponse(
                200,
                headers={"content-type": "application/vnd.amazon.eventstream"},
            ),
        ]
    )

    events = [
        event
        async for event in BedrockAdapter().stream_anthropic(
            cast(httpx.AsyncClient, client),
            request,
            {
                "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
                "AWS_REGION": "us-east-1",
                "AWS_MODEL_ID": "bedrock",
            },
        )
    ]

    assert len(client.calls) == 2
    first_payload = orjson.loads(cast(bytes, client.calls[0]["content"]))
    second_payload = orjson.loads(cast(bytes, client.calls[1]["content"]))
    assert first_payload["additionalModelRequestFields"] == {
        "reasoning_effort": "medium"
    }
    assert "additionalModelRequestFields" not in second_payload
    assert events[0].event == "message_start"


@pytest.mark.anyio
async def test_bedrock_streaming_adapter_retries_forced_tool_choice_thinking_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_iter_events(_: httpx.Response):
        for event in (
            {"messageStart": {"conversationId": "conv-1"}},
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "ok"}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {"metadata": {"usage": {"outputTokens": 3}}},
        ):
            yield event

    def _fake_request_components(
        _: NormalizedMessagesRequest,
        __: dict[str, str],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        assert stream is True
        return {
            "url": "https://bedrock.invalid/model/bedrock/converse-stream",
            "body": orjson.dumps(
                {
                    "messages": [{"role": "user", "content": [{"text": "hello"}]}],
                    "additionalModelRequestFields": {
                        "thinking": {"type": "enabled", "budget_tokens": 2048}
                    },
                    "toolConfig": {"toolChoice": {"tool": {"name": "lookup"}}},
                }
            ),
        }

    def _fake_signed_request(
        *,
        request: NormalizedMessagesRequest,
        credentials: dict[str, str],
        url: str,
        body: bytes,
    ) -> ProviderHTTPRequest:
        del request, credentials
        return ProviderHTTPRequest(
            method="POST",
            url=url,
            headers={"Content-Type": "application/json"},
            body=body,
            stream=True,
        )

    monkeypatch.setattr(
        provider_bedrock, "_iter_bedrock_stream_events", _fake_iter_events
    )
    monkeypatch.setattr(
        provider_bedrock, "_bedrock_request_components", _fake_request_components
    )
    monkeypatch.setattr(
        provider_bedrock, "_signed_bedrock_request", _fake_signed_request
    )

    request = _base_request(
        provider="bedrock",
        model="bedrock",
        messages=(NormalizedMessage(role="user", content="hello"),),
        model_settings={"thinking": {"type": "enabled", "budget_tokens": 2048}},
        tools=(
            {
                "name": "lookup",
                "input_schema": {"type": "object"},
            },
        ),
        tool_choice={"type": "tool", "name": "lookup"},
    )
    client = _FakeBedrockStreamingClient(
        [
            _FakeBedrockStreamResponse(
                400,
                headers={"content-type": "application/json"},
                chunks=[
                    b'{"message":"The model returned the following errors: '
                    b'Thinking may not be enabled when tool_choice forces tool use."}'
                ],
            ),
            _FakeBedrockStreamResponse(
                200,
                headers={"content-type": "application/vnd.amazon.eventstream"},
            ),
        ]
    )

    events = [
        event
        async for event in BedrockAdapter().stream_anthropic(
            cast(httpx.AsyncClient, client),
            request,
            {
                "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
                "AWS_REGION": "us-east-1",
                "AWS_MODEL_ID": "bedrock",
            },
        )
    ]

    assert len(client.calls) == 2
    first_payload = orjson.loads(cast(bytes, client.calls[0]["content"]))
    second_payload = orjson.loads(cast(bytes, client.calls[1]["content"]))
    assert first_payload["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }
    assert "additionalModelRequestFields" not in second_payload
    assert second_payload["toolConfig"]["toolChoice"] == {"tool": {"name": "lookup"}}
    assert events[0].event == "message_start"


@pytest.mark.anyio
async def test_bedrock_streaming_adapter_translates_text_tool_and_thinking_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_iter_events(_: httpx.Response):
        for event in (
            {"messageStart": {"conversationId": "conv-1"}},
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hel"}}},
            {
                "contentBlockStart": {
                    "contentBlockIndex": 1,
                    "start": {
                        "toolUse": {
                            "toolUseId": "call_1",
                            "name": "lookup",
                        }
                    },
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 1,
                    "delta": {"toolUse": {"input": '{"query":"status"}'}},
                }
            },
            {
                "contentBlockStart": {
                    "contentBlockIndex": 2,
                    "start": {"reasoningContent": {"text": "", "signature": ""}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 2,
                    "delta": {"reasoningContent": {"text": "Thinking..."}},
                },
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"contentBlockStop": {"contentBlockIndex": 1}},
            {"contentBlockStop": {"contentBlockIndex": 2}},
            {"metadata": {"usage": {"inputTokens": 7, "outputTokens": 5}}},
            {"messageStop": {"stopReason": "tool_use"}},
        ):
            yield event

    monkeypatch.setattr(
        provider_bedrock, "_iter_bedrock_stream_events", _fake_iter_events
    )

    request = _base_request(
        provider="bedrock",
        model="anthropic.claude-3-7-sonnet",
        messages=(NormalizedMessage(role="user", content="hello"),),
    )
    client = cast(
        httpx.AsyncClient,
        _FakeBedrockStreamingClient(_FakeBedrockStreamResponse()),
    )

    events = [
        event
        async for event in BedrockAdapter().stream_anthropic(
            client,
            request,
            {
                "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token",
                "AWS_REGION": "us-east-1",
                "AWS_MODEL_ID": "anthropic.claude-3-7-sonnet",
            },
        )
    ]

    assert [event.event for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_start",
        "content_block_delta",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_stop",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[0].payload["message"]["id"] == "msg_conv-1"
    assert events[2].payload["delta"]["text"] == "Hel"
    assert events[3].payload["content_block"]["type"] == "tool_use"
    assert events[4].payload["delta"]["partial_json"] == '{"query":"status"}'
    assert events[5].payload["content_block"]["type"] == "thinking"
    assert events[6].payload["delta"]["thinking"] == "Thinking..."
    assert events[10].payload["delta"]["stop_reason"] == "tool_use"
    assert events[10].payload["usage"]["output_tokens"] == 5

    stream_call = cast(_FakeBedrockStreamingClient, client).calls[0]
    assert str(stream_call["url"]).endswith(
        "/model/anthropic.claude-3-7-sonnet/converse-stream"
    )


def test_bedrock_json_payload_fallback_extracts_final_response_events() -> None:
    events = provider_bedrock._bedrock_json_payload_to_events(  # pyright: ignore[reportPrivateUsage]
        {
            "Output": {
                "Message": {
                    "Content": [
                        {"Text": "hello"},
                        {
                            "ToolUse": {
                                "ToolUseId": "call_1",
                                "Name": "lookup",
                                "Input": {"query": "status"},
                            }
                        },
                    ]
                }
            },
            "Usage": {"inputTokens": 3, "outputTokens": 5},
            "StopReason": "tool_use",
        }
    )

    assert events[0] == {"messageStart": {"conversationId": "bedrock-json"}}
    assert events[1] == {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
    assert events[2]["contentBlockDelta"]["delta"]["text"] == "hello"
    assert events[4]["contentBlockStart"]["start"]["toolUse"] == {
        "toolUseId": "call_1",
        "name": "lookup",
    }
    assert events[5]["contentBlockDelta"]["delta"]["toolUse"]["input"] == (
        '{"query":"status"}'
    )
    assert events[-2] == {"metadata": {"usage": {"inputTokens": 3, "outputTokens": 5}}}
    assert events[-1] == {"messageStop": {"stopReason": "tool_use"}}


def test_bedrock_parsed_stream_event_handles_converse_event_shape() -> None:
    parser = _FakeEventStreamJSONParser({"messageStart": {"role": "assistant"}})
    raw_event = _FakeRawBedrockEvent(
        {
            "status_code": 200,
            "headers": {},
            "body": b'{"role":"assistant"}',
        }
    )

    event = provider_bedrock._parsed_bedrock_stream_event(  # pyright: ignore[reportPrivateUsage]
        raw_event,
        parser=parser,
    )

    assert event == {"messageStart": {"role": "assistant"}}


def test_bedrock_response_stream_shape_uses_converse_stream_output() -> None:
    shape = provider_bedrock._bedrock_response_stream_shape()  # pyright: ignore[reportPrivateUsage]

    assert "messageStart" in shape.members
    assert "contentBlockDelta" in shape.members
    assert "messageStop" in shape.members
    assert "chunk" not in shape.members


@pytest.mark.anyio
async def test_bedrock_json_stream_error_payload_raises_provider_error() -> None:
    response = cast(
        httpx.Response,
        _FakeBedrockStreamResponse(
            headers={"content-type": "application/json"},
            chunks=[
                orjson.dumps(
                    {
                        "Output": {
                            "__type": "com.amazon.coral.service#ValidationException",
                            "message": "This model does not support streaming.",
                        }
                    }
                )
            ],
        ),
    )

    with pytest.raises(RuntimeError, match="ValidationException"):
        async for _ in provider_bedrock._iter_bedrock_stream_events(  # pyright: ignore[reportPrivateUsage]
            response
        ):
            pass
