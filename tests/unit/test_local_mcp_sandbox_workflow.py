"""Unit tests for local MCP stdio sandbox transport config."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracecat.agent.mcp.sandbox import workflow as workflow_module


def _build_target(**overrides: object) -> SimpleNamespace:
    base = {
        "mcp_integration_id": uuid4(),
        "stdio_command": "npx",
        "stdio_args": ["@modelcontextprotocol/server-github"],
        "timeout": 30,
        "sandbox_allow_network": False,
        "sandbox_egress_allowlist": None,
        "sandbox_egress_denylist": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_stdio_client_config_warns_on_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(workflow_module.config, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(workflow_module, "_DIRECT_SANDBOX_WARNING_EMITTED", False)
    caplog.set_level(logging.WARNING)

    server_config, temp_dir = workflow_module._build_stdio_client_config(
        target=_build_target(),
        stdio_env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )

    assert server_config["command"] == "npx"
    assert temp_dir is None
    assert workflow_module._DIRECT_SANDBOX_WARNING_EMITTED is True


def test_build_stdio_client_config_wraps_with_nsjail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nsjail_path = tmp_path / "nsjail"
    nsjail_path.write_text("")
    rootfs_path = tmp_path / "rootfs"
    for subdir in ("usr", "lib", "bin", "etc"):
        (rootfs_path / subdir).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(workflow_module.config, "TRACECAT__DISABLE_NSJAIL", False)
    monkeypatch.setattr(
        workflow_module.config, "TRACECAT__SANDBOX_NSJAIL_PATH", str(nsjail_path)
    )
    monkeypatch.setattr(
        workflow_module.config, "TRACECAT__SANDBOX_ROOTFS_PATH", str(rootfs_path)
    )
    monkeypatch.setattr(
        workflow_module,
        "_resolve_stdio_command_path",
        lambda command, env: "/usr/local/bin/npx",
    )

    stdio_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "cache" / "home"),
    }
    server_config, temp_dir = workflow_module._build_stdio_client_config(
        target=_build_target(
            sandbox_allow_network=True,
            sandbox_egress_allowlist=["api.github.com:443"],
        ),
        stdio_env=stdio_env,
    )

    assert server_config["command"] == str(nsjail_path)
    assert "--config" in server_config["args"]
    assert "TRACECAT__MCP_SANDBOX_EGRESS_ALLOWLIST" in server_config["env"]
    assert temp_dir is not None
    assert (temp_dir / "nsjail.cfg").exists()
