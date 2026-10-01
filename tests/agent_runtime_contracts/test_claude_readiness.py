"""Real pinned SDK/CLI contracts with synthetic MCP and model endpoints.

See README.md for the compatibility matrix and the limits of this evidence.
"""

import asyncio
import importlib.metadata
import subprocess
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import claude_agent_sdk
import orjson
import pytest

from tests.agent_runtime_contracts.runtime_fixture import runtime_case


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("transport_kind", ["http", "stdio"])
@pytest.mark.parametrize(
    "provider",
    [
        "anthropic",
        "bedrock",
        "custom-model-provider",
        "ollama",
        "vllm",
        "litellm",
        "openrouter",
    ],
)
@pytest.mark.parametrize("child", [False, True], ids=["root", "direct-child"])
async def test_required_tool_is_callable_without_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport_kind: str,
    provider: str,
    child: bool,
):
    async with runtime_case(
        tmp_path,
        monkeypatch,
        transport_kind=transport_kind,
        provider=provider,
        child=child,
    ) as (runtime, payload, writer, model):
        async with asyncio.timeout(45):
            await runtime.run(payload)
    assert not writer.send_error.called, writer.send_error.call_args_list
    assert len(model.requests) >= 2
    requests = [
        r
        for r in model.requests
        if not child
        or "CHILD_READINESS_SENTINEL" in orjson.dumps(r.get("system")).decode()
    ]
    assert len(requests) >= 2, [
        (r["model"], [t["name"] for t in r.get("tools", [])]) for r in model.requests
    ]
    first_tools = {tool["name"]: tool for tool in requests[0]["tools"]}
    server = model.server
    selected = first_tools[f"mcp__{server}__required_lookup"]
    assert selected.get("defer_loading") is not True
    assert selected["input_schema"]["properties"]["device_id"]["type"] == "string"
    if provider == "bedrock" or child:
        assert "ToolSearch" not in first_tools
        assert f"mcp__{server}__unrelated_lookup" in first_tools
    else:
        assert f"mcp__{server}__unrelated_lookup" not in first_tools
        assert "ToolSearch" in first_tools
        assert (
            f"mcp__{server}__unrelated_lookup"
            in orjson.dumps(requests[0]["messages"]).decode()
        )
    assert "fixture-device:device-123" in orjson.dumps(requests[1]["messages"]).decode()
    assert writer.send_result.called
    assert writer.send_stream_event.called


@pytest.mark.anyio
@pytest.mark.parametrize("transport_kind", ["http", "stdio"])
@pytest.mark.parametrize("eager_mode", ["none", "server"])
async def test_server_loading_and_unmarked_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport_kind: str,
    eager_mode: str,
):
    async with runtime_case(
        tmp_path, monkeypatch, transport_kind=transport_kind, eager_mode=eager_mode
    ) as (runtime, payload, writer, model):
        async with asyncio.timeout(45):
            await runtime.run(payload)
    assert not writer.send_error.called, writer.send_error.call_args_list
    tools = {t["name"]: t for t in model.requests[0]["tools"]}
    required = f"mcp__{model.server}__required_lookup"
    unrelated = f"mcp__{model.server}__unrelated_lookup"
    if eager_mode == "none":
        assert required not in tools
        assert unrelated not in tools
        assert "ToolSearch" in tools
        loaded = {t["name"]: t for t in model.requests[1]["tools"]}
        assert required in loaded
        assert unrelated not in loaded
    else:
        assert required in tools
        assert unrelated in tools
    assert (
        "fixture-device:device-123"
        in orjson.dumps(model.requests[-1]["messages"]).decode()
    )


@pytest.mark.anyio
async def test_eager_tool_still_requires_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    async with runtime_case(tmp_path, monkeypatch) as (runtime, payload, writer, model):
        payload.config.tool_approvals = {"required_lookup": True}
        async with asyncio.timeout(45):
            await runtime.run(payload)
    approvals = [
        item
        for call in writer.send_stream_event.call_args_list
        for item in (call.args[0].approval_items or [])
    ]
    assert [item.id for item in approvals] == ["fixture_call"]
    assert "fixture-device:device-123" not in orjson.dumps(model.requests).decode()
    assert not writer.send_error.called, writer.send_error.call_args_list
    assert writer.send_done.called


@pytest.mark.anyio
async def test_native_session_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    async with runtime_case(tmp_path, monkeypatch) as (runtime, payload, writer, model):
        async with asyncio.timeout(45):
            await runtime.run(payload)
        calls = writer.send_session_line.call_args_list
        assert calls, "CLI session history must be emitted for continuation"
        resumed = replace(
            payload,
            sdk_session_id=calls[0].args[0],
            sdk_session_data="\n".join(call.args[1] for call in calls),
            user_prompt="Continue the synthetic fixture.",
        )
        before = len(model.requests)
        async with asyncio.timeout(45):
            await runtime.run(resumed)
    assert not writer.send_error.called, writer.send_error.call_args_list
    assert len(model.requests) > before
    history = orjson.dumps(model.requests[before]["messages"]).decode()
    assert "fixture-device:device-123" in history
    assert "Continue the synthetic fixture." in history
    assert writer.send_result.call_count == 2


@pytest.mark.anyio
async def test_cancel_while_model_request_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    async with runtime_case(tmp_path, monkeypatch, transport_kind="stdio") as (
        runtime,
        payload,
        writer,
        model,
    ):
        model.hold = True
        task = asyncio.create_task(runtime.run(payload))
        try:
            async with asyncio.timeout(45):
                await model.arrived.wait()
            async with asyncio.timeout(10):
                await runtime.interrupt(reason="user_cancel")
                await task
        finally:
            model.release.set()
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert writer.send_done.called
    assert not writer.send_error.called, writer.send_error.call_args_list
    assert "fixture-device:device-123" not in orjson.dumps(model.requests).decode()


def test_pinned_runtime_versions():
    assert importlib.metadata.version("claude-agent-sdk") == "0.2.160"
    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert (
        subprocess.check_output([str(cli), "--version"], text=True, timeout=10).strip()
        == "2.1.283 (Claude Code)"
    )
