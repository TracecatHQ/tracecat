"""Local scripted Anthropic wire endpoint for real-CLI contract tests."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import orjson
from aiohttp import web

from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime


class ModelFixture:
    """Record real CLI wire requests and return a deterministic tool call.

    Anthropic's wire protocol is open JSON; Any is intentional at this test-only
    boundary so we can assert the exact installed CLI output without modeling it.
    """

    def __init__(
        self,
        *,
        child: bool = False,
        transport_kind: str = "http",
        discovery: bool = False,
    ) -> None:
        self.discovery = discovery
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = False
        self.child = child
        self.server = (
            ("subagent-fixture-child-fixture-tools" if child else "fixture-tools")
            if transport_kind == "stdio"
            else (
                ClaudeAgentRuntime._subagent_registry_server_name("fixture-child")
                if child
                else "tracecat-registry"
            )
        )
        self.child_calls = 0
        self.child_result_received = asyncio.Event()
        self.requests: list[dict[str, Any]] = []

    async def messages(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        self.arrived.set()
        if self.hold:
            await self.release.wait()
        child_request = (
            self.child
            and "CHILD_READINESS_SENTINEL" in orjson.dumps(body.get("system")).decode()
        )
        if child_request:
            self.child_calls += 1
            if self.child_calls >= 2:
                self.child_result_received.set()
        elif self.child and len(self.requests) > 1:
            # This CLI launches Agent asynchronously. Keep the root alive until
            # the child sends its tool result, or SDK teardown cancels its hooks.
            async with asyncio.timeout(20):
                await self.child_result_received.wait()
        if self.discovery and len(self.requests) == 1:
            content = {
                "type": "tool_use",
                "id": "search_call",
                "name": "ToolSearch",
                "input": {"query": f"select:mcp__{self.server}__required_lookup"},
            }
            reason = "tool_use"
        elif self.child and len(self.requests) == 1:
            content = {
                "type": "tool_use",
                "id": "delegate_call",
                "name": "Agent",
                "input": {
                    "subagent_type": "fixture-child",
                    "description": "Check readiness",
                    "prompt": "Use required_lookup for device-123.",
                },
            }
            reason = "tool_use"
        elif (
            not self.child and len(self.requests) == (2 if self.discovery else 1)
        ) or (child_request and self.child_calls == 1):
            content = {
                "type": "tool_use",
                "id": "fixture_call",
                "name": "mcp__tracecat-registry__required_lookup",
                "input": {"device_id": "device-123"},
            }
            content["name"] = f"mcp__{self.server}__required_lookup"
            reason = "tool_use"
        else:
            content = {"type": "text", "text": "Fixture complete."}
            reason = "end_turn"
        message = {
            "id": f"msg_fixture_{len(self.requests)}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [content],
            "stop_reason": reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if not body.get("stream"):
            return web.json_response(message)
        if request.transport is None or request.transport.is_closing():
            return web.Response(status=499)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        start = {**message, "content": [], "stop_reason": None}
        block = (
            {**content, "input": {}}
            if content["type"] == "tool_use"
            else {"type": "text", "text": ""}
        )
        delta = (
            {
                "type": "input_json_delta",
                "partial_json": orjson.dumps(content["input"]).decode(),
            }
            if content["type"] == "tool_use"
            else {"type": "text_delta", "text": content["text"]}
        )
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": block},
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": delta},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": reason, "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        for name, data in events:
            await response.write(
                b"event: " + name.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n"
            )
        await response.write_eof()
        return response

    @asynccontextmanager
    async def serve(self) -> AsyncIterator[str]:
        app = web.Application()
        app.router.add_post("/v1/messages", self.messages)
        runner = web.AppRunner(app)
        await runner.setup()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        site = web.SockSite(runner, listener)
        await site.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            await runner.cleanup()
