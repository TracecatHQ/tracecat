from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from tracecat.auth import saml


@pytest.mark.anyio
async def test_get_org_saml_metadata_url_prefers_env_in_single_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_metadata_url = "https://env-idp.example.com/metadata"
    get_setting_mock = AsyncMock()
    fake_session = AsyncMock()

    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(saml, "SAML_IDP_METADATA_URL", env_metadata_url)
    monkeypatch.setattr(saml, "get_setting", get_setting_mock)

    result = await saml.get_org_saml_metadata_url(fake_session, uuid.uuid4())

    assert result == env_metadata_url
    get_setting_mock.assert_not_called()


@pytest.mark.anyio
async def test_get_org_saml_metadata_url_uses_db_settings_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_metadata_url = "https://env-idp.example.com/metadata"
    db_metadata_url = "https://db-idp.example.com/metadata"
    get_setting_mock = AsyncMock(return_value=db_metadata_url)
    fake_session = AsyncMock()

    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(saml, "SAML_IDP_METADATA_URL", env_metadata_url)
    monkeypatch.setattr(saml, "get_setting", get_setting_mock)

    result = await saml.get_org_saml_metadata_url(fake_session, uuid.uuid4())

    assert result == db_metadata_url
    assert get_setting_mock.await_count == 1
    assert get_setting_mock.await_args is not None
    assert get_setting_mock.await_args.kwargs["default"] == env_metadata_url
