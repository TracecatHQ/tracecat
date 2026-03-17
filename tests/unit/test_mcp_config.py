from __future__ import annotations

import importlib

import pytest

import tracecat.mcp.config as mcp_config


def test_mcp_startup_retry_settings_parse_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    monkeypatch.setenv("TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", "1.25")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__STARTUP_MAX_ATTEMPTS == 5
    assert reloaded.TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS == 1.25


def test_removed_auth_mode_env_has_no_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    monkeypatch.setenv("TRACECAT_MCP__AUTH_MODE", "none")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_MODE is reloaded.MCPAuthMode.OIDC


def test_mcp_auth_methods_default_to_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACECAT_MCP__AUTH_METHODS", raising=False)
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_MODE is reloaded.MCPAuthMode.OIDC


def test_mcp_auth_mode_parses_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "none")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_MODE is reloaded.MCPAuthMode.NONE


def test_mcp_auth_mode_blank_env_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "")

    with pytest.raises(
        ValueError,
        match="TRACECAT_MCP__AUTH_METHODS must include at least one method",
    ):
        importlib.reload(mcp_config)

    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    importlib.reload(mcp_config)


def test_mcp_auth_mode_rejects_multiple_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc,none")

    with pytest.raises(
        ValueError,
        match="TRACECAT_MCP__AUTH_METHODS must be one of: oidc, none",
    ):
        importlib.reload(mcp_config)

    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    importlib.reload(mcp_config)


def test_mcp_auth_mode_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "api_key")

    with pytest.raises(
        ValueError,
        match="TRACECAT_MCP__AUTH_METHODS must be one of: oidc, none",
    ):
        importlib.reload(mcp_config)

    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    importlib.reload(mcp_config)
