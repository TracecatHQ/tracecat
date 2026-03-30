from __future__ import annotations

import importlib

import pytest

import tracecat.mcp.config as mcp_config


def test_mcp_startup_retry_settings_parse_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", "1.25")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__STARTUP_MAX_ATTEMPTS == 5
    assert reloaded.TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS == 1.25
