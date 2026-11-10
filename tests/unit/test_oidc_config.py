"""Tests for platform OIDC client configuration."""

import importlib
import logging
from unittest.mock import MagicMock, patch

import pytest

from tracecat import config
from tracecat.auth import oidc
from tracecat.auth.enums import AuthType
from tracecat.auth.oidc import create_platform_oauth_client, oidc_auth_type_enabled


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


@pytest.fixture
def clear_oidc_secret_cache():
    oidc._retrieve_client_secret.cache_clear()
    yield
    oidc._retrieve_client_secret.cache_clear()


def _mock_secretsmanager(response: dict[str, str]) -> MagicMock:
    client = MagicMock()
    client.get_secret_value.return_value = response
    return client


@pytest.mark.usefixtures("clear_oidc_secret_cache")
def test_oidc_client_secret_arn_is_read_once_and_wins(monkeypatch) -> None:
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET__ARN", "arn:test")
    client = _mock_secretsmanager({"SecretString": "arn-secret\n"})

    with patch("tracecat.auth.oidc.boto3.session.Session") as mock_session:
        mock_session.return_value.client.return_value = client
        first = oidc.get_platform_oidc_client_secret()
        second = oidc.get_platform_oidc_client_secret()

    assert first == "arn-secret"
    assert second == "arn-secret"
    client.get_secret_value.assert_called_once_with(SecretId="arn:test")


@pytest.mark.usefixtures("clear_oidc_secret_cache")
def test_oidc_client_secret_without_arn_uses_env(monkeypatch) -> None:
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET__ARN", None)

    with patch("tracecat.auth.oidc.boto3.session.Session") as mock_session:
        assert oidc.get_platform_oidc_client_secret() == "env-secret"

    mock_session.assert_not_called()


@pytest.mark.usefixtures("clear_oidc_secret_cache")
def test_oidc_client_secret_arn_rejects_empty_secret(monkeypatch) -> None:
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET__ARN", "arn:test")
    client = _mock_secretsmanager({"SecretString": "  "})

    with patch("tracecat.auth.oidc.boto3.session.Session") as mock_session:
        mock_session.return_value.client.return_value = client
        with pytest.raises(ValueError, match="OIDC client secret"):
            oidc.get_platform_oidc_client_secret()


@pytest.mark.usefixtures("clear_oidc_secret_cache")
def test_oidc_client_secret_arn_reads_secret_binary(monkeypatch) -> None:
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET__ARN", "arn:test")
    client = _mock_secretsmanager({"SecretBinary": "YmluYXJ5LXNlY3JldA=="})

    with patch("tracecat.auth.oidc.boto3.session.Session") as mock_session:
        mock_session.return_value.client.return_value = client
        assert oidc.get_platform_oidc_client_secret() == "binary-secret"
