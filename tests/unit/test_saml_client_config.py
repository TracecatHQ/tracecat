from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tracecat.auth import saml


class _FakeSaml2Config:
    loaded: dict[str, Any] | None = None

    def load(self, settings: dict[str, Any]) -> None:
        self.loaded = settings


class _FakeSaml2Client:
    def __init__(self, _config: _FakeSaml2Config):
        self.metadata = {"idp": {"entity_id": "example-idp"}}


@pytest.mark.anyio
async def test_create_saml_client_prefers_env_metadata_url_in_single_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_metadata_url = "https://env-idp.example.com/metadata"
    get_setting_mock = AsyncMock()
    fake_config = _FakeSaml2Config()

    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(saml, "SAML_IDP_METADATA_URL", env_metadata_url)
    monkeypatch.setattr(saml, "SAML_CA_CERTS", None)
    monkeypatch.setattr(saml, "Saml2Config", lambda: fake_config)
    monkeypatch.setattr(saml, "Saml2Client", _FakeSaml2Client)
    monkeypatch.setattr(saml, "get_setting", get_setting_mock)

    await saml.create_saml_client()

    assert fake_config.loaded is not None
    assert fake_config.loaded["metadata"]["remote"][0]["url"] == env_metadata_url
    get_setting_mock.assert_not_called()


@pytest.mark.anyio
async def test_create_saml_client_uses_db_settings_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_metadata_url = "https://env-idp.example.com/metadata"
    db_metadata_url = "https://db-idp.example.com/metadata"
    get_setting_mock = AsyncMock(return_value=db_metadata_url)
    fake_config = _FakeSaml2Config()

    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(saml, "SAML_IDP_METADATA_URL", env_metadata_url)
    monkeypatch.setattr(saml, "SAML_CA_CERTS", None)
    monkeypatch.setattr(saml, "Saml2Config", lambda: fake_config)
    monkeypatch.setattr(saml, "Saml2Client", _FakeSaml2Client)
    monkeypatch.setattr(saml, "get_setting", get_setting_mock)

    await saml.create_saml_client()

    assert fake_config.loaded is not None
    assert fake_config.loaded["metadata"]["remote"][0]["url"] == db_metadata_url
    assert get_setting_mock.await_count == 1
    assert get_setting_mock.await_args.kwargs["default"] == env_metadata_url
