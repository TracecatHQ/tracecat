from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

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
    assert get_setting_mock.await_args.kwargs["default"] is None


@pytest.mark.anyio
async def test_get_org_saml_metadata_url_rejects_missing_db_value_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_setting_mock = AsyncMock(return_value=None)
    fake_session = AsyncMock()

    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(saml, "SAML_IDP_METADATA_URL", "https://env-idp.example.com")
    monkeypatch.setattr(saml, "get_setting", get_setting_mock)

    with pytest.raises(HTTPException) as exc_info:
        await saml.get_org_saml_metadata_url(fake_session, uuid.uuid4())

    assert exc_info.value.status_code == 400


class _FakeScalars:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def all(self) -> list[str]:
        return self._values


class _FakeResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._values)


@pytest.mark.anyio
async def test_should_allow_email_for_org_denies_no_domains_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([])
    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", True)

    allowed = await saml.should_allow_email_for_org(
        fake_session, uuid.uuid4(), "user@example.com"
    )

    assert allowed is False


@pytest.mark.anyio
async def test_should_allow_email_for_org_allows_no_domains_in_single_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([])
    monkeypatch.setattr(saml, "TRACECAT__EE_MULTI_TENANT", False)

    allowed = await saml.should_allow_email_for_org(
        fake_session, uuid.uuid4(), "user@example.com"
    )

    assert allowed is True
