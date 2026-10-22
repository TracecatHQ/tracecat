"""The bundled Claude Code CLI recovers from a managed gateway stream cut.

A LiteLLM pod that goes away mid-response used to fail the whole agent turn:
the LLM socket proxy fired its fatal callback and the executor cancelled the
turn. The proxy now ends the stream with a clean EOF and lets the CLI recover
the model call. These tests run the real path: bundled CLI -> sandbox socket
bridge -> LLM socket proxy -> a fake gateway that resets the connection
mid-stream.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, cast
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    create_sdk_mcp_server,
    tool,
)

from tracecat.agent.adapter.vercel import TextDeltaEventPayload, VercelStreamContext
from tracecat.agent.runtime.claude_code.adapter import ClaudeSDKAdapter
from tracecat.agent.runtime.claude_code.runtime import (
    MODEL_REQUEST_FAILED_MESSAGE,
    ClaudeAgentRuntime,
)
from tracecat.agent.sandbox.llm_proxy import (
    LLMProxyError,
    LLMRoute,
    LLMRoutingPlan,
    LLMSocketProxy,
)
from tracecat.agent.sandbox.shim_entrypoint import (
    LLM_MAX_BODY_SIZE,
    SandboxSocketBridge,
)

_TOOL_NAME = "mcp__counter__count"
_DEAD_TEXT = "Text from the dead stream. "
_INTRO_TEXT = "Counting now. "
_FINAL_TEXT = "Counted exactly once."

type _Fault = Literal["after_tool_use", "after_text", "after_text_always", "mid_text"]


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _message_start(message_id: str, model: str) -> bytes:
    return _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
    )


def _text_block(index: int, text: str, *, complete: bool = True) -> list[bytes]:
    events = [
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
    ]
    if complete:
        events.append(
            _sse("content_block_stop", {"type": "content_block_stop", "index": index})
        )
    return events


def _tool_use_block(index: int, tool_use_id: str) -> list[bytes]:
    return [
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": _TOOL_NAME,
                    "input": {},
                },
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def _message_end(stop_reason: str) -> list[bytes]:
    return [
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        _sse("message_stop", {"type": "message_stop"}),
    ]


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    match messages[-1:]:
        case [{"role": "user", "content": list(blocks)}]:
            return any(block.get("type") == "tool_result" for block in blocks)
        case _:
            return False


@dataclass(slots=True)
class _GatewayState:
    """What the fake gateway saw.

    The first main streaming request gets the fault; ``after_text_always``
    faults every one.
    """

    fault: _Fault
    main_requests: list[list[dict[str, Any]]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class _GatewayHandler(BaseHTTPRequestHandler):
    """Anthropic Messages stand-in for the LiteLLM gateway."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        path = self.path.split("?", 1)[0]
        if path == "/v1/messages/count_tokens":
            self._json(200, {"input_tokens": 10})
            return
        if path != "/v1/messages":
            self._json(404, {"type": "error", "error": {"type": "not_found_error"}})
            return

        state = cast(_GatewayServer, self.server).state
        messages: list[dict[str, Any]] = body.get("messages") or []
        model = body.get("model") or "fake-model"
        is_main = any(t.get("name") == _TOOL_NAME for t in body.get("tools") or [])
        main_index = 0
        if is_main:
            with state.lock:
                state.main_requests.append(messages)
                main_index = len(state.main_requests)
        message_id = f"msg_fake_{main_index}_{threading.get_ident()}"
        wants_final = not is_main or _has_tool_result(messages)
        final_text = _FINAL_TEXT if is_main else "side request"

        if not body.get("stream"):
            content: list[dict[str, Any]] = (
                [{"type": "text", "text": final_text}]
                if wants_final
                else [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{message_id}",
                        "name": _TOOL_NAME,
                        "input": {},
                    }
                ]
            )
            self._json(
                200,
                {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": content,
                    "stop_reason": "end_turn" if wants_final else "tool_use",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            )
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        events = [_message_start(message_id, model)]
        if main_index == 1 or state.fault == "after_text_always":
            # Faulted response: never reaches message_delta or message_stop.
            match state.fault:
                case "after_tool_use":
                    events += _text_block(0, _INTRO_TEXT)
                    events += _tool_use_block(1, f"toolu_{message_id}")
                case "after_text" | "after_text_always":
                    events += _text_block(0, _DEAD_TEXT)
                case "mid_text":
                    events += _text_block(0, _DEAD_TEXT, complete=False)
            for event in events:
                self._write_chunk(event)
            self._reset_connection()
            return
        if wants_final:
            events += _text_block(0, final_text) + _message_end("end_turn")
        else:
            events += _text_block(0, _INTRO_TEXT)
            events += _tool_use_block(1, f"toolu_{message_id}")
            events += _message_end("tool_use")
        for event in events:
            self._write_chunk(event)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _reset_connection(self) -> None:
        """Drop the socket mid-body, like a gateway pod going away."""
        # Give the CLI's streaming tool executor time to start the tool block.
        threading.Event().wait(0.3)
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()
        self.close_connection = True


class _GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: _GatewayState) -> None:
        super().__init__(("127.0.0.1", 0), _GatewayHandler)
        self.state = state


@dataclass(slots=True)
class _TurnOutcome:
    result: ResultMessage | None
    tool_calls: int
    ui_text: str
    proxy_errors: list[LLMProxyError]
    main_requests: list[list[dict[str, Any]]]


@pytest.fixture
def short_dir() -> Iterator[Path]:
    # pytest's per-test directory can exceed macOS's Unix socket path limit.
    with TemporaryDirectory() as directory:
        yield Path(directory)


async def _run_turn(fault: _Fault, root: Path) -> _TurnOutcome:
    state = _GatewayState(fault=fault)
    gateway = _GatewayServer(state)
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()

    proxy_errors: list[LLMProxyError] = []
    proxy = LLMSocketProxy(
        socket_path=root / "llm.sock",
        routing_plan=LLMRoutingPlan(
            managed_route=LLMRoute(
                base_url=f"http://127.0.0.1:{gateway.server_address[1]}",
                model_provider="anthropic",
                mode="managed",
            ),
            direct_routes={},
        ),
        on_error=proxy_errors.append,
    )
    bridge = SandboxSocketBridge(
        socket_path=root / "llm.sock",
        max_body_size=LLM_MAX_BODY_SIZE,
        on_uds_failure="error",
        log_label="LLM bridge",
    )

    tool_calls = 0

    @tool("count", "Count one call. Call it exactly once.", {})
    async def count(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal tool_calls
        tool_calls += 1
        return {"content": [{"type": "text", "text": "counted"}]}

    home = root / "home"
    work = root / "work"
    home.mkdir()
    work.mkdir()
    result: ResultMessage | None = None
    stream_adapter = ClaudeSDKAdapter()
    ui = VercelStreamContext(message_id=None)
    ui_text: list[str] = []
    try:
        await proxy.start()
        bridge_port = await bridge.start()
        options = ClaudeAgentOptions(
            model="claude-sonnet-4-5",
            cwd=str(work),
            include_partial_messages=True,
            setting_sources=["user"],
            allowed_tools=[_TOOL_NAME],
            mcp_servers={"counter": create_sdk_mcp_server("counter", tools=[count])},
            max_turns=4,
            env={
                "HOME": str(home),
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{bridge_port}",
                "ANTHROPIC_API_KEY": "sk-ant-fake-key",
                "ANTHROPIC_AUTH_TOKEN": "fake-gateway-token",
                "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "ENABLE_TOOL_SEARCH": "false",
            },
        )
        async with asyncio.timeout(120), ClaudeSDKClient(options=options) as client:
            await client.query("Call the count tool once, then reply.")
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    result = message
                    continue
                # Mirror the runtime: every non-ping stream event feeds the UI.
                if not isinstance(message, StreamEvent):
                    continue
                if message.event.get("type") == "ping":
                    continue
                unified = stream_adapter.to_unified_event(message)
                async for payload in ui.handle_event(unified):
                    if isinstance(payload, TextDeltaEventPayload):
                        ui_text.append(payload.delta)
    finally:
        await bridge.stop()
        await proxy.stop()
        gateway.shutdown()
        gateway.server_close()
        gateway_thread.join(timeout=5)

    return _TurnOutcome(
        result=result,
        tool_calls=tool_calls,
        ui_text="".join(ui_text),
        proxy_errors=proxy_errors,
        main_requests=state.main_requests,
    )


@pytest.mark.anyio
async def test_cut_after_complete_tool_use_continues_turn(short_dir: Path) -> None:
    """Completed blocks are kept: the tool runs once and the turn continues."""
    outcome = await _run_turn("after_tool_use", short_dir)

    assert outcome.proxy_errors == []
    assert outcome.result is not None
    assert outcome.result.is_error is False
    assert outcome.result.result is not None
    assert _FINAL_TEXT in outcome.result.result
    assert outcome.tool_calls == 1
    # The follow-up request carries the result; the tool call is not re-issued.
    assert len(outcome.main_requests) == 2
    assert _has_tool_result(outcome.main_requests[1])
    assert outcome.ui_text.count(_INTRO_TEXT) == 1
    assert outcome.ui_text.count(_FINAL_TEXT) == 1


@pytest.mark.anyio
async def test_cut_inside_first_block_restreams_same_request(short_dir: Path) -> None:
    """Nothing completed: the CLI re-issues the same request and finishes."""
    outcome = await _run_turn("mid_text", short_dir)

    assert outcome.proxy_errors == []
    assert outcome.result is not None
    assert outcome.result.is_error is False
    assert outcome.result.result is not None
    assert _FINAL_TEXT in outcome.result.result
    assert outcome.tool_calls == 1
    assert len(outcome.main_requests) == 3
    assert outcome.main_requests[1] == outcome.main_requests[0]
    assert _has_tool_result(outcome.main_requests[2])
    # Each delta reaches the UI once. The dead message's partial text was
    # already streamed and stays visible: the UI stream protocol cannot retract
    # it. Session history only persists completed blocks.
    assert outcome.ui_text.count(_DEAD_TEXT) == 1
    assert outcome.ui_text.count(_INTRO_TEXT) == 1
    assert outcome.ui_text.count(_FINAL_TEXT) == 1


@pytest.mark.anyio
async def test_cut_after_complete_text_keeps_it_and_continues(short_dir: Path) -> None:
    """A completed text block is kept and the CLI asks the model to go on."""
    outcome = await _run_turn("after_text", short_dir)

    assert outcome.proxy_errors == []
    assert outcome.result is not None
    assert outcome.result.is_error is False
    assert outcome.result.result is not None
    assert _FINAL_TEXT in outcome.result.result
    assert outcome.tool_calls == 1
    assert len(outcome.main_requests) == 3
    # The kept text is sent back as the model's own partial answer.
    assert outcome.main_requests[1] != outcome.main_requests[0]
    assert _DEAD_TEXT in json.dumps(outcome.main_requests[1])
    assert outcome.ui_text.count(_DEAD_TEXT) == 1
    assert outcome.ui_text.count(_FINAL_TEXT) == 1


@pytest.mark.anyio
async def test_repeated_cuts_after_complete_text_fail_the_turn(short_dir: Path) -> None:
    """When the CLI runs out of continuations, the runtime fails the turn.

    Every response is cut after a completed block, so the proxy suppresses every
    interruption and never reaches its fatal path. The CLI gives up with an API
    error result that must not be recorded as the turn's answer.
    """
    outcome = await _run_turn("after_text_always", short_dir)

    assert outcome.proxy_errors == []
    assert outcome.tool_calls == 0
    assert outcome.result is not None
    assert outcome.result.is_error is True
    assert outcome.result.subtype == "success"

    writer = AsyncMock()
    runtime = ClaudeAgentRuntime(writer, transport_factory=lambda _: AsyncMock())
    await runtime._handle_result_message(outcome.result)

    writer.send_result.assert_awaited_once()
    assert writer.send_result.await_args.kwargs["output"] is None
    writer.send_error.assert_awaited_once_with(MODEL_REQUEST_FAILED_MESSAGE)
