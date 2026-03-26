from __future__ import annotations

import pytest

from tracecat.auth.dex.mode import MCPDexMode, get_mcp_dex_mode


def test_get_mcp_dex_mode_prefers_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "basic")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES", frozenset()
    )

    assert get_mcp_dex_mode() is MCPDexMode.BASIC


def test_get_mcp_dex_mode_prefers_explicit_oidc_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "oidc")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset(),
    )

    assert get_mcp_dex_mode() is MCPDexMode.OIDC


def test_get_mcp_dex_mode_selects_oidc_with_complete_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"oidc", "basic"}),
    )
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.OIDC_ISSUER",
        "https://issuer.example.com",
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.OIDC_CLIENT_SECRET",
        "client-secret",
    )

    assert get_mcp_dex_mode() is MCPDexMode.OIDC


def test_get_mcp_dex_mode_falls_back_to_basic_without_oidc_connector_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"basic", "oidc"}),
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_ISSUER", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_ID", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_SECRET", "")

    assert get_mcp_dex_mode() is MCPDexMode.BASIC


def test_get_mcp_dex_mode_returns_none_without_supported_dex_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"saml"}),
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_ISSUER", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_ID", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_SECRET", "")

    assert get_mcp_dex_mode() is None
