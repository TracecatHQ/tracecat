from __future__ import annotations

import importlib

import pytest

import tracecat.mcp.config as mcp_config


def test_mcp_auth_mode_defaults_to_oidc_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACECAT_MCP__AUTH_MODE", raising=False)
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_MODE == reloaded.MCPAuthMode.OIDC_INTERACTIVE


@pytest.mark.parametrize(
    ("env_value", "expected_mode"),
    [
        ("oidc_interactive", "OIDC_INTERACTIVE"),
        ("oauth_client_credentials_jwt", "OAUTH_CLIENT_CREDENTIALS_JWT"),
        (
            "oauth_client_credentials_introspection",
            "OAUTH_CLIENT_CREDENTIALS_INTROSPECTION",
        ),
    ],
)
def test_mcp_auth_mode_parses_valid_values(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_mode: str,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_MODE", env_value)
    reloaded = importlib.reload(mcp_config)

    assert reloaded.TRACECAT_MCP__AUTH_MODE == reloaded.MCPAuthMode[expected_mode]


def test_mcp_auth_mode_raises_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT_MCP__AUTH_MODE", "invalid_mode")

    with pytest.raises(
        ValueError,
        match="Invalid value for TRACECAT_MCP__AUTH_MODE",
    ):
        importlib.reload(mcp_config)

    monkeypatch.delenv("TRACECAT_MCP__AUTH_MODE", raising=False)
    importlib.reload(mcp_config)
