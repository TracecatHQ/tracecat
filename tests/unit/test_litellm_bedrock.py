"""Exercise Bedrock authentication and tool streams through LiteLLM's real adapter."""

import struct
import zlib
from collections.abc import AsyncIterator
from unittest.mock import Mock

import httpx
import orjson
import pytest
from botocore.credentials import Credentials
from litellm.anthropic_interface import acreate
from litellm.llms.bedrock.base_aws_llm import BaseAWSLLM
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

from tracecat.agent.gateway import _inject_provider_credentials


def _event_frame(event_type: str, payload: dict[str, object]) -> bytes:
    """Encode a real AWS event-stream frame, including both CRC checksums."""
    headers = bytearray()
    for name, value in {
        ":message-type": "event",
        ":event-type": event_type,
        ":content-type": "application/json",
    }.items():
        key, text = name.encode(), value.encode()
        headers.extend(bytes([len(key)]) + key + b"\x07")
        headers.extend(struct.pack(">H", len(text)) + text)
    body = orjson.dumps(payload)
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


@pytest.mark.anyio
@pytest.mark.parametrize("bearer", [True, False], ids=["bearer", "iam"])
@pytest.mark.parametrize("stream", [False, True], ids=["response", "stream"])
async def test_bedrock_converse_tool_call(
    monkeypatch: pytest.MonkeyPatch, bearer: bool, stream: bool
) -> None:
    # Bearer-only deployments have no SigV4 principal. LiteLLM 1.100.0
    # dereferenced this None before dispatching either kind of request.
    credentials = None if bearer else Credentials("synthetic-key", "synthetic-secret")
    monkeypatch.setattr(BaseAWSLLM, "get_credentials", Mock(return_value=credentials))
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.delenv("LITELLM_RUST", raising=False)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        expected_path = "converse-stream" if stream else "converse"
        assert request.url.path.endswith(f"/{expected_path}")
        if bearer:
            assert request.headers["authorization"] == "Bearer synthetic-token"
        else:
            assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
        body = orjson.loads(request.content)
        assert body["toolConfig"]["tools"][0]["toolSpec"]["name"] == "echo"
        if not stream:
            return httpx.Response(
                200,
                json={
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": "synthetic-call",
                                        "name": "echo",
                                        "input": {"value": "ok"},
                                    }
                                }
                            ],
                        }
                    },
                    "stopReason": "tool_use",
                    "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
                    "metrics": {"latencyMs": 1},
                },
            )
        events: list[tuple[str, dict[str, object]]] = [
            ("messageStart", {"role": "assistant"}),
            (
                "contentBlockStart",
                {
                    "contentBlockIndex": 0,
                    "start": {
                        "toolUse": {"toolUseId": "synthetic-call", "name": "echo"}
                    },
                },
            ),
            (
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"value":'}}},
            ),
            (
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '"ok"}'}}},
            ),
            ("contentBlockStop", {"contentBlockIndex": 0}),
            ("messageStop", {"stopReason": "tool_use"}),
            (
                "metadata",
                {
                    "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
                    "metrics": {"latencyMs": 1},
                },
            ),
        ]
        return httpx.Response(
            200,
            headers={"content-type": "application/vnd.amazon.eventstream"},
            content=b"".join(_event_frame(kind, payload) for kind, payload in events),
        )

    data = {}
    _inject_provider_credentials(
        data,
        "bedrock",
        {
            "AWS_REGION": "us-east-1",
            "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
            "AWS_BEDROCK_USE_CONVERSE": "true",
            **(
                {"AWS_BEARER_TOKEN_BEDROCK": "synthetic-token"}
                if bearer
                else {
                    "AWS_ACCESS_KEY_ID": "synthetic-key",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-secret",
                }
            ),
        },
    )
    handler = AsyncHTTPHandler()
    await handler.client.aclose()
    handler.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        result = await acreate(
            **data,
            messages=[{"role": "user", "content": "Call echo with value ok"}],
            tools=[
                {
                    "name": "echo",
                    "input_schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                }
            ],
            max_tokens=32,
            stream=stream,
            client=handler,
            num_retries=0,
            max_retries=0,
        )
        if stream:
            assert isinstance(result, AsyncIterator)
            wire = b"".join([chunk async for chunk in result])
            events = [
                orjson.loads(line.removeprefix(b"data: "))
                for line in wire.splitlines()
                if line.startswith(b"data: ")
            ]
            tool = next(
                event["content_block"]
                for event in events
                if event["type"] == "content_block_start"
                and event["content_block"]["type"] == "tool_use"
            )
            assert tool["id"] == "synthetic-call"
            assert tool["name"] == "echo"
            arguments = "".join(
                event["delta"]["partial_json"]
                for event in events
                if event["type"] == "content_block_delta"
                and event["delta"]["type"] == "input_json_delta"
            )
            assert orjson.loads(arguments) == {"value": "ok"}
            assert any(
                event["type"] == "message_delta"
                and event["delta"]["stop_reason"] == "tool_use"
                for event in events
            )
            assert events[-1]["type"] == "message_stop"
        else:
            assert isinstance(result, dict)
            assert result.get("stop_reason") == "tool_use"
            content = result.get("content")
            assert content is not None
            tool_calls = []
            for block in content:
                match block:
                    case {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": args,
                    }:
                        tool_calls.append((call_id, name, args))
            assert tool_calls == [("synthetic-call", "echo", {"value": "ok"})]
        assert len(requests) == 1
    finally:
        await handler.close()
