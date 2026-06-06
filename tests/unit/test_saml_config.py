"""Tests for SAML security configuration."""

from __future__ import annotations

import importlib

import pytest

from tracecat import config


def _reload_config() -> None:
    importlib.reload(config)


def test_config_treats_blank_saml_signing_env_as_secure_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic,saml")
        env.setenv("SAML_SIGNED_ASSERTIONS", "")
        env.setenv("SAML_SIGNED_RESPONSES", "")
        env.delenv("OIDC_ISSUER", raising=False)

        _reload_config()

        assert config.SAML_SIGNED_ASSERTIONS is True
        assert config.SAML_SIGNED_RESPONSES is True

    _reload_config()


def test_config_fails_closed_when_saml_enabled_without_signature_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic,saml")
        env.setenv("SAML_SIGNED_ASSERTIONS", "false")
        env.setenv("SAML_SIGNED_RESPONSES", "false")
        env.delenv("OIDC_ISSUER", raising=False)

        with pytest.raises(ValueError, match="SAML SSO requires signed assertions"):
            _reload_config()

    _reload_config()


def test_config_allows_disabled_saml_with_unsigned_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic")
        env.setenv("SAML_SIGNED_ASSERTIONS", "false")
        env.setenv("SAML_SIGNED_RESPONSES", "false")
        env.delenv("OIDC_ISSUER", raising=False)

        _reload_config()

        assert config.SAML_SIGNED_ASSERTIONS is False
        assert config.SAML_SIGNED_RESPONSES is False

    _reload_config()


def test_config_rejects_invalid_saml_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as env:
        env.setenv("TRACECAT__AUTH_TYPES", "basic")
        env.setenv("SAML_SIGNED_ASSERTIONS", "definitely")
        env.delenv("OIDC_ISSUER", raising=False)

        with pytest.raises(
            ValueError, match="SAML_SIGNED_ASSERTIONS must be a boolean"
        ):
            _reload_config()

    _reload_config()
