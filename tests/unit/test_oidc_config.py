"""Tests for platform OIDC client configuration."""

import importlib
import logging
from unittest.mock import MagicMock, patch

import pytest

from tracecat import config
from tracecat.auth.enums import AuthType
from tracecat.auth.oidc import create_platform_oauth_client, oidc_auth_type_enabled
from tracecat.auth.users import UserManager


@pytest.fixture(autouse=True)
def required_base_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", "test-db-encryption-key")
    monkeypatch.setenv("TRACECAT__SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("TRACECAT__SIGNING_SECRET", "test-signing-secret")
    monkeypatch.setenv("USER_AUTH_SECRET", "test-user-auth-secret")


def test_oidc_auth_type_enabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC})
    assert oidc_auth_type_enabled() is False

    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.OIDC})
    assert oidc_auth_type_enabled() is True


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


def test_config_defaults_to_basic_when_auth_types_unset(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.delenv("TRACECAT__AUTH_TYPES", raising=False)
        env.delenv("OIDC_ISSUER", raising=False)
        importlib.reload(config)

        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC}

    importlib.reload(config)


def test_config_ignores_removed_google_oauth_auth_type(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic,google_oauth")
        env.delenv("OIDC_ISSUER", raising=False)
        with caplog.at_level(logging.WARNING):
            importlib.reload(config)

        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC}
        assert "Ignoring removed auth type 'google_oauth'" in caplog.text

    importlib.reload(config)


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
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "")
        env.delenv("OIDC_ISSUER", raising=False)
        importlib.reload(config)

        assert config.TRACECAT__AUTH_TYPES == {AuthType.BASIC}

    importlib.reload(config)


def test_config_requires_issuer_when_oidc_enabled(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "oidc")
        env.delenv("OIDC_ISSUER", raising=False)
        env.setenv("OIDC_CLIENT_ID", "oidc-client-id")
        env.setenv("OIDC_CLIENT_SECRET", "oidc-client-secret")
        with pytest.raises(
            ValueError,
            match="OIDC_ISSUER must be set when TRACECAT__AUTH_TYPES includes 'oidc'",
        ):
            importlib.reload(config)

    importlib.reload(config)


def test_config_keeps_oauth_aliases_for_oidc_credentials(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic")
        env.delenv("OIDC_CLIENT_ID", raising=False)
        env.delenv("OIDC_CLIENT_SECRET", raising=False)
        env.setenv("OAUTH_CLIENT_ID", "legacy-client-id")
        env.setenv("OAUTH_CLIENT_SECRET", "legacy-client-secret")
        env.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        env.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        importlib.reload(config)

        assert config.OIDC_CLIENT_ID == "legacy-client-id"
        assert config.OIDC_CLIENT_SECRET == "legacy-client-secret"

    importlib.reload(config)


def test_config_allows_missing_user_auth_secret_when_auth_type_does_not_use_it(
    monkeypatch,
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "saml")
        env.delenv("USER_AUTH_SECRET", raising=False)
        importlib.reload(config)

    importlib.reload(config)


def test_config_requires_oidc_client_secret_when_oidc_enabled(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "oidc")
        env.setenv("OIDC_ISSUER", "https://auth.example.com")
        env.setenv("OIDC_CLIENT_ID", "oidc-client-id")
        env.delenv("OIDC_CLIENT_SECRET", raising=False)
        env.delenv("OAUTH_CLIENT_SECRET", raising=False)
        with pytest.raises(
            KeyError,
            match="OIDC_CLIENT_SECRET must be set when TRACECAT__AUTH_TYPES includes 'oidc'",
        ):
            importlib.reload(config)

    importlib.reload(config)


@pytest.mark.parametrize(
    "var_name",
    [
        "USER_AUTH_SECRET",
        "TRACECAT__DB_ENCRYPTION_KEY",
        "TRACECAT__SIGNING_SECRET",
        "TRACECAT__SERVICE_KEY",
    ],
)
def test_config_allows_missing_core_tracecat_secrets(
    monkeypatch, var_name: str
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "saml")
        env.delenv(var_name, raising=False)
        importlib.reload(config)

    importlib.reload(config)


def test_user_manager_requires_user_auth_secret_when_basic_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC})
    monkeypatch.setattr(config, "USER_AUTH_SECRET", "")

    with pytest.raises(
        KeyError,
        match="USER_AUTH_SECRET must be set when TRACECAT__AUTH_TYPES includes 'basic'",
    ):
        UserManager(MagicMock())


def test_user_manager_allows_missing_user_auth_secret_when_basic_disabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.SAML})
    monkeypatch.setattr(config, "USER_AUTH_SECRET", "")

    manager = UserManager(MagicMock())

    assert manager.reset_password_token_secret == ""
    assert manager.verification_token_secret == ""


def test_config_ignores_google_oauth_env_aliases(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic")
        env.delenv("OIDC_CLIENT_ID", raising=False)
        env.delenv("OIDC_CLIENT_SECRET", raising=False)
        env.delenv("OAUTH_CLIENT_ID", raising=False)
        env.delenv("OAUTH_CLIENT_SECRET", raising=False)
        env.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id")
        env.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret")
        importlib.reload(config)

        assert config.OIDC_CLIENT_ID == ""
        assert config.OIDC_CLIENT_SECRET == ""

    importlib.reload(config)
