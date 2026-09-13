from __future__ import annotations

from unittest.mock import Mock

import orjson
import pytest

from tracecat.agent import service as service_module
from tracecat.network import DisallowedUrlError


def test_vertex_credentials_accept_canonical_google_token_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = Mock(token="access-token")
    credentials_factory = Mock(return_value=credentials)
    request = object()
    request_factory = Mock(return_value=request)
    monkeypatch.setattr(
        service_module.service_account.Credentials,
        "from_service_account_info",
        credentials_factory,
    )
    monkeypatch.setattr(service_module, "GoogleAuthRequest", request_factory)
    credentials_blob = orjson.dumps(
        {
            "type": "service_account",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ).decode()

    token = service_module._refresh_vertex_token(credentials_blob)

    assert token == "access-token"
    credentials_factory.assert_called_once()
    credentials.refresh.assert_called_once_with(request)


def test_vertex_credentials_reject_custom_token_uri_before_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_factory = Mock()
    request_factory = Mock()
    monkeypatch.setattr(
        service_module.service_account.Credentials,
        "from_service_account_info",
        credentials_factory,
    )
    monkeypatch.setattr(service_module, "GoogleAuthRequest", request_factory)
    credentials_blob = orjson.dumps(
        {
            "type": "service_account",
            "token_uri": "http://127.0.0.1:1/internal",
        }
    ).decode()

    with pytest.raises(DisallowedUrlError, match="token URL is not allowed"):
        service_module._refresh_vertex_token(credentials_blob)

    credentials_factory.assert_not_called()
    request_factory.assert_not_called()


def test_vertex_credentials_reject_external_account_split_parser_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_factory = Mock()
    request_factory = Mock()
    monkeypatch.setattr(
        service_module.service_account.Credentials,
        "from_service_account_info",
        credentials_factory,
    )
    monkeypatch.setattr(service_module, "GoogleAuthRequest", request_factory)
    credentials_blob = orjson.dumps(
        {
            "type": "external_account",
            "token_uri": "https://oauth2.googleapis.com/token",
            "token_url": "http://127.0.0.1:2/sts",
            "credential_source": {"url": "http://127.0.0.1:1/metadata"},
        }
    ).decode()

    with pytest.raises(DisallowedUrlError, match="must be a service account"):
        service_module._refresh_vertex_token(credentials_blob)

    credentials_factory.assert_not_called()
    request_factory.assert_not_called()
