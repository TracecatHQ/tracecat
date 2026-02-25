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


def test_removed_auth_mode_env_has_no_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_MODE", "oauth_client_credentials_jwt")
    reloaded = importlib.reload(mcp_config)

    assert not hasattr(reloaded, "TRACECAT_MCP__AUTH_MODE")
