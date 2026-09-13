from __future__ import annotations

from unittest.mock import Mock

import orjson
import pytest

from tracecat.integrations.providers.google.service_account import (
    GOOGLE_TOKEN_URL,
    GoogleServiceAccountOAuthProvider,
)
from tracecat.network import DisallowedUrlError


def _credentials_json(**overrides: object) -> str:
    return orjson.dumps(
        {
            "type": "service_account",
            "private_key": "synthetic-private-key",
            "client_email": "service-account@example.test",
            "token_uri": GOOGLE_TOKEN_URL,
            **overrides,
        }
    ).decode()


def test_google_service_account_accepts_canonical_token_uri() -> None:
    provider = GoogleServiceAccountOAuthProvider(
        client_secret=_credentials_json(),
    )

    assert provider.service_account_info["type"] == "service_account"
    assert provider.service_account_info["token_uri"] == GOOGLE_TOKEN_URL
    assert provider.token_endpoint == GOOGLE_TOKEN_URL


@pytest.mark.parametrize(
    "token_uri",
    [
        "https://token.example.test/oauth",
        "http://127.0.0.1:1/internal",
        None,
    ],
)
def test_google_service_account_rejects_noncanonical_token_uri_before_refresh(
    monkeypatch: pytest.MonkeyPatch,
    token_uri: str | None,
) -> None:
    credentials_factory = Mock()
    request_factory = Mock()
    monkeypatch.setattr(
        "tracecat.integrations.providers.google.service_account."
        "service_account.Credentials.from_service_account_info",
        credentials_factory,
    )
    monkeypatch.setattr(
        "tracecat.integrations.providers.google.service_account.Request",
        request_factory,
    )

    with pytest.raises(DisallowedUrlError, match="token URL is not allowed"):
        GoogleServiceAccountOAuthProvider(
            client_secret=_credentials_json(token_uri=token_uri),
        )

    credentials_factory.assert_not_called()
    request_factory.assert_not_called()


def test_google_service_account_rejects_external_account_before_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_factory = Mock()
    request_factory = Mock()
    monkeypatch.setattr(
        "tracecat.integrations.providers.google.service_account."
        "service_account.Credentials.from_service_account_info",
        credentials_factory,
    )
    monkeypatch.setattr(
        "tracecat.integrations.providers.google.service_account.Request",
        request_factory,
    )

    with pytest.raises(ValueError, match="must be a service account"):
        GoogleServiceAccountOAuthProvider(
            client_secret=_credentials_json(type="external_account"),
        )

    credentials_factory.assert_not_called()
    request_factory.assert_not_called()
