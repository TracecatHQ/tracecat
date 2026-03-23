"""Tests for platform OIDC client configuration."""

import importlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from tracecat import config
from tracecat.auth.enums import AuthType
from tracecat.auth.oidc import (
    create_platform_oauth_client,
    get_mcp_oidc_config,
    oidc_auth_type_enabled,
    oidc_login_configured,
)


@contextmanager
def reload_config_with_env(
    monkeypatch: pytest.MonkeyPatch,
    **env: str | None,
) -> Iterator[None]:
    try:
        with monkeypatch.context() as env_patch:
            for key, value in env.items():
                if value is None:
                    env_patch.delenv(key, raising=False)
                else:
                    env_patch.setenv(key, value)
            importlib.reload(config)
            yield
    finally:
        importlib.reload(config)


def test_oidc_auth_type_enabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC})
    assert oidc_auth_type_enabled() is False

    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC, AuthType.OIDC})
    monkeypatch.setattr(config, "OIDC_ISSUER", "")
    assert oidc_auth_type_enabled() is True

    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.OIDC})
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://auth.example.com")
    assert oidc_auth_type_enabled() is True


def test_oidc_login_configured(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC})
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://auth.example.com")
    assert oidc_login_configured() is False

    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC, AuthType.OIDC})
    monkeypatch.setattr(config, "OIDC_ISSUER", "")
    assert oidc_login_configured() is False

    monkeypatch.setattr(config, "OIDC_ISSUER", "https://auth.example.com")
    assert oidc_login_configured() is True


def test_create_platform_oauth_client_uses_openid_when_issuer_set(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://auth.example.com")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "oidc-client-id")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "oidc-client-secret")
    monkeypatch.setattr(config, "OIDC_SCOPES", ("openid", "email", "profile"))

    with patch("tracecat.auth.oidc.OpenID", autospec=True) as mock_openid:
        mock_client = object()
        mock_openid.return_value = mock_client

        client = create_platform_oauth_client()

    assert client is mock_client
    mock_openid.assert_called_once_with(
        client_id="oidc-client-id",
        client_secret="oidc-client-secret",
        openid_configuration_endpoint=(
            "https://auth.example.com/.well-known/openid-configuration"
        ),
        name="oidc",
        base_scopes=["openid", "email", "profile"],
    )


def test_create_platform_oauth_client_requires_issuer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "OIDC_ISSUER", "")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "legacy-client-id")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "legacy-client-secret")
    monkeypatch.setattr(config, "OIDC_SCOPES", ("openid", "email"))

    with patch("tracecat.auth.oidc.OpenID", autospec=True) as mock_openid:
        with pytest.raises(ValueError, match="OIDC_ISSUER must be configured"):
            create_platform_oauth_client()

    mock_openid.assert_not_called()


def test_get_mcp_oidc_config_uses_platform_scopes(monkeypatch) -> None:
    monkeypatch.setattr(config, "OIDC_SCOPES", ("openid", "profile", "email"))
    monkeypatch.setattr(config, "DEX_ISSUER", "https://dex.example.com/")
    monkeypatch.setattr(config, "DEX_TRACECAT_CLIENT_ID", "tracecat-mcp")
    monkeypatch.setattr(config, "DEX_TRACECAT_CLIENT_SECRET", "secret")

    oidc_config = get_mcp_oidc_config()

    assert oidc_config.issuer == "https://dex.example.com"
    assert oidc_config.client_id == "tracecat-mcp"
    assert oidc_config.client_secret == "secret"
    assert oidc_config.scopes == ("openid", "profile", "email")


def test_config_defaults_to_basic_when_auth_types_unset(monkeypatch) -> None:
    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES=None,
        OIDC_ISSUER=None,
    ):
        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC}


def test_config_ignores_removed_google_oauth_auth_type(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES="basic,google_oauth",
        OIDC_ISSUER=None,
    ):
        with caplog.at_level(logging.WARNING):
            importlib.reload(config)

        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC}
        assert "Ignoring removed auth type 'google_oauth'" in caplog.text


def test_config_rejects_removed_google_oauth_only_auth_type(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "google_oauth")
        env.delenv("OIDC_ISSUER", raising=False)
        with pytest.raises(
            ValueError,
            match="TRACECAT__AUTH_TYPES must include at least one supported auth type",
        ):
            importlib.reload(config)

    importlib.reload(config)


def test_config_rejects_empty_auth_types(monkeypatch) -> None:
    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES="",
        OIDC_ISSUER=None,
    ):
        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC}


def test_config_requires_issuer_when_oidc_enabled(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "oidc")
        env.delenv("OIDC_ISSUER", raising=False)
        with pytest.raises(
            ValueError,
            match="OIDC_ISSUER must be set when TRACECAT__AUTH_TYPES is exactly 'oidc'",
        ):
            importlib.reload(config)

    importlib.reload(config)


def test_config_does_not_require_issuer_for_mixed_auth_modes(monkeypatch) -> None:
    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES="basic,oidc",
        OIDC_ISSUER=None,
    ):
        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC, AuthType.OIDC}
        assert config.OIDC_ISSUER == ""


def test_config_keeps_oauth_aliases_for_oidc_credentials(monkeypatch) -> None:
    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES="basic",
        OIDC_CLIENT_ID=None,
        OIDC_CLIENT_SECRET=None,
        OAUTH_CLIENT_ID="legacy-client-id",
        OAUTH_CLIENT_SECRET="legacy-client-secret",
        GOOGLE_OAUTH_CLIENT_ID=None,
        GOOGLE_OAUTH_CLIENT_SECRET=None,
    ):
        assert config.OIDC_CLIENT_ID == "legacy-client-id"
        assert config.OIDC_CLIENT_SECRET == "legacy-client-secret"


def test_config_ignores_google_oauth_env_aliases(monkeypatch) -> None:
    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES="basic",
        OIDC_CLIENT_ID=None,
        OIDC_CLIENT_SECRET=None,
        OAUTH_CLIENT_ID=None,
        OAUTH_CLIENT_SECRET=None,
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    ):
        assert config.OIDC_CLIENT_ID == ""
        assert config.OIDC_CLIENT_SECRET == ""


def test_reload_config_with_env_restores_config_after_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_client_id = config.OIDC_CLIENT_ID

    with reload_config_with_env(
        monkeypatch,
        TRACECAT__AUTH_TYPES="basic",
        OIDC_CLIENT_ID="test-oidc-client-id",
        OAUTH_CLIENT_ID=None,
    ):
        assert config.OIDC_CLIENT_ID == "test-oidc-client-id"

    assert config.OIDC_CLIENT_ID == original_client_id
