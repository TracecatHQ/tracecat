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
    monkeypatch.setenv("TRACECAT_MCP__AUTH_MODE", "oauth_client_credentials_jwt")
    reloaded = importlib.reload(mcp_config)

    assert not hasattr(reloaded, "TRACECAT_MCP__AUTH_MODE")


def test_mcp_auth_methods_default_to_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACECAT_MCP__AUTH_METHODS", raising=False)
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_METHODS == frozenset({"oidc"})


def test_mcp_auth_methods_parse_multiple_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc, api_key ,none")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_METHODS == frozenset({"oidc", "api_key", "none"})


def test_mcp_auth_methods_blank_env_falls_back_to_oidc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_METHODS == frozenset({"oidc"})


def test_mcp_auth_methods_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc,nope")

    with pytest.raises(
        ValueError,
        match="TRACECAT_MCP__AUTH_METHODS contains invalid values: nope",
    ):
        importlib.reload(mcp_config)

    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    importlib.reload(mcp_config)


def test_mcp_api_key_blank_env_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_METHODS", "oidc")
    monkeypatch.setenv("TRACECAT_MCP__API_KEY", "")
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__API_KEY is None
