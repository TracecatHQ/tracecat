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


def test_get_mcp_dex_mode_requires_full_saml_connector_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"saml"}),
    )
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.SAML_IDP_METADATA_URL",
        "https://idp.example.com/metadata",
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_SAML_SSO_URL", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_SAML_CA_DATA", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.SAML_METADATA_CERT", "")

    assert get_mcp_dex_mode() is None


def test_get_mcp_dex_mode_selects_saml_with_complete_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"saml", "oidc", "basic"}),
    )
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.SAML_IDP_METADATA_URL",
        "https://idp.example.com/metadata",
    )
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.MCP_DEX_SAML_SSO_URL",
        "https://idp.example.com/sso",
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_DEX_SAML_CA_DATA", "PEM")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.SAML_METADATA_CERT", "")

    assert get_mcp_dex_mode() is MCPDexMode.SAML
