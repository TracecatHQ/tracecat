from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import tracecat.agent.sandbox.nsjail as nsjail_module


class _FakeProcess:
    pass


@pytest.mark.anyio
async def test_spawned_claude_shim_uses_explicit_stdio_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(nsjail_module, "TRACECAT__DISABLE_NSJAIL", True)
    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(
        *_args: object,
        **kwargs: object,
    ) -> _FakeProcess:
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        nsjail_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()

    await nsjail_module.spawn_jailed_runtime(
        socket_dir=socket_dir,
        init_payload_path=tmp_path / "init.json",
        pipe_stdin=True,
    )

    assert captured["kwargs"]["limit"] == nsjail_module.CLAUDE_SHIM_STDIO_LIMIT_BYTES
