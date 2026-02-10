"""Tests for platform OIDC client configuration."""

from unittest.mock import patch

from tracecat import config
from tracecat.auth.enums import AuthType
from tracecat.auth.oidc import create_platform_oauth_client, oidc_auth_type_enabled


def test_oidc_auth_type_enabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC})
    assert oidc_auth_type_enabled() is False

    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.OIDC})
    assert oidc_auth_type_enabled() is True

    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.GOOGLE_OAUTH})
    assert oidc_auth_type_enabled() is True


def test_create_platform_oauth_client_uses_openid_when_issuer_set(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://auth.example.com")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "oidc-client-id")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "oidc-client-secret")
    monkeypatch.setattr(config, "OIDC_SCOPES", ("openid", "email", "profile"))

    with (
        patch("tracecat.auth.oidc.OpenID", autospec=True) as mock_openid,
        patch("tracecat.auth.oidc.GoogleOAuth2", autospec=True) as mock_google,
    ):
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
    mock_google.assert_not_called()


def test_create_platform_oauth_client_falls_back_to_google_client(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "OIDC_ISSUER", "")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "legacy-client-id")
    monkeypatch.setattr(config, "OIDC_CLIENT_SECRET", "legacy-client-secret")
    monkeypatch.setattr(config, "OIDC_SCOPES", ("openid", "email"))

    with (
        patch("tracecat.auth.oidc.OpenID", autospec=True) as mock_openid,
        patch("tracecat.auth.oidc.GoogleOAuth2", autospec=True) as mock_google,
    ):
        mock_client = object()
        mock_google.return_value = mock_client

        client = create_platform_oauth_client()

    assert client is mock_client
    mock_google.assert_called_once_with(
        client_id="legacy-client-id",
        client_secret="legacy-client-secret",
        scopes=["openid", "email"],
        name="oidc",
    )
    mock_openid.assert_not_called()
