from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
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
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values

    def first(self) -> object | None:
        return self._values[0] if self._values else None


class _FakeResult:
    def __init__(self, values: list[object]) -> None:
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


@pytest.mark.anyio
async def test_get_pending_org_invitation_returns_latest_match() -> None:
    fake_session = AsyncMock()
    invitation = SimpleNamespace(token="inv-123")
    fake_session.execute.return_value = _FakeResult([invitation])

    result = await saml.get_pending_org_invitation(
        fake_session, uuid.uuid4(), "user@example.com"
    )

    assert result is invitation


@pytest.mark.anyio
async def test_select_authorized_email_prefers_pending_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    invitation = SimpleNamespace(token="inv-123")
    get_invitation_mock = AsyncMock(return_value=invitation)
    should_allow_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)
    monkeypatch.setattr(saml, "should_allow_email_for_org", should_allow_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["invitee@example.com"]
    )

    assert email == "invitee@example.com"
    assert pending_invitation is invitation
    should_allow_mock.assert_not_called()


@pytest.mark.anyio
async def test_select_authorized_email_falls_back_to_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    monkeypatch.setattr(
        saml, "get_pending_org_invitation", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        saml, "should_allow_email_for_org", AsyncMock(return_value=True)
    )

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["user@example.com"]
    )

    assert email == "user@example.com"
    assert pending_invitation is None


@pytest.mark.anyio
async def test_select_authorized_email_rejects_when_not_allowlisted_or_invited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    monkeypatch.setattr(
        saml, "get_pending_org_invitation", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        saml, "should_allow_email_for_org", AsyncMock(return_value=False)
    )

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["user@example.com"]
    )

    assert email is None
    assert pending_invitation is None


def test_should_allow_saml_user_auto_provisioning_for_superadmin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(saml, "TRACECAT__AUTH_SUPERADMIN_EMAIL", "admin@example.com")

    assert (
        saml.should_allow_saml_user_auto_provisioning(
            email="admin@example.com", pending_invitation=None
        )
        is True
    )
    assert (
        saml.should_allow_saml_user_auto_provisioning(
            email="user@example.com", pending_invitation=None
        )
        is False
    )


def test_should_allow_saml_user_auto_provisioning_for_pending_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(saml, "TRACECAT__AUTH_SUPERADMIN_EMAIL", None)
    invitation = cast(saml.OrganizationInvitation, SimpleNamespace(token="inv-123"))

    assert (
        saml.should_allow_saml_user_auto_provisioning(
            email="user@example.com",
            pending_invitation=invitation,
        )
        is True
    )
