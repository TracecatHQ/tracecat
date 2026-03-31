from __future__ import annotations

import pytest

from tracecat.auth.dex.mode import (
    MCPAuthMode,
    get_login_auth_types,
    get_mcp_auth_mode,
    login_auth_type_enabled,
)
from tracecat.auth.enums import AuthType


def test_get_mcp_auth_mode_prefers_explicit_basic_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "basic")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES", frozenset()
    )

    assert get_mcp_auth_mode() is MCPAuthMode.BASIC


def test_get_mcp_auth_mode_prefers_explicit_oidc_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "oidc")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset(),
    )

    assert get_mcp_auth_mode() is MCPAuthMode.OIDC


def test_get_mcp_auth_mode_prefers_explicit_saml_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "saml")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset(),
    )

    assert get_mcp_auth_mode() is MCPAuthMode.SAML


def test_get_mcp_auth_mode_selects_oidc_with_complete_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "")
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

    assert get_mcp_auth_mode() is MCPAuthMode.OIDC


def test_get_mcp_auth_mode_falls_back_to_basic_without_oidc_connector_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"basic", "oidc"}),
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_ISSUER", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_ID", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_SECRET", "")

    assert get_mcp_auth_mode() is MCPAuthMode.BASIC


def test_get_mcp_auth_mode_falls_back_to_saml_when_only_saml_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"saml"}),
    )
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_ISSUER", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_ID", "")
    monkeypatch.setattr("tracecat.auth.dex.mode.config.OIDC_CLIENT_SECRET", "")

    assert get_mcp_auth_mode() is MCPAuthMode.SAML


def test_get_login_auth_types_uses_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "saml")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({"basic", "oidc", "saml"}),
    )

    assert get_login_auth_types() == [AuthType.SAML]
    assert login_auth_type_enabled(AuthType.SAML) is True
    assert login_auth_type_enabled(AuthType.OIDC) is False


def test_get_login_auth_types_preserves_platform_auth_types_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.auth.dex.mode.config.MCP_AUTH_MODE", "")
    monkeypatch.setattr(
        "tracecat.auth.dex.mode.config.TRACECAT__AUTH_TYPES",
        frozenset({AuthType.BASIC, AuthType.OIDC}),
    )

    assert get_login_auth_types() == [AuthType.BASIC, AuthType.OIDC]
    assert login_auth_type_enabled(AuthType.BASIC) is True
    assert login_auth_type_enabled(AuthType.OIDC) is True
