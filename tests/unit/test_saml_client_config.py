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

    def scalar_one(self) -> object:
        if not self._values:
            raise AssertionError("Expected at least one value")
        return self._values[0]


@pytest.mark.anyio
async def test_should_allow_email_for_org_denies_no_domains_in_multi_tenant() -> None:
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([])

    allowed = await saml.should_allow_email_for_org(
        fake_session, uuid.uuid4(), "user@example.com"
    )

    assert allowed is False


@pytest.mark.anyio
async def test_should_allow_email_for_org_denies_no_domains_in_single_tenant() -> None:
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([])

    allowed = await saml.should_allow_email_for_org(
        fake_session, uuid.uuid4(), "user@example.com"
    )

    assert allowed is False


@pytest.mark.anyio
async def test_should_allow_email_for_org_allows_allowlisted_domain() -> None:
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult(["example.com"])

    allowed = await saml.should_allow_email_for_org(
        fake_session, uuid.uuid4(), "user@example.com"
    )

    assert allowed is True


@pytest.mark.anyio
async def test_should_allow_email_for_org_denies_non_allowlisted_domain() -> None:
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult(["example.com"])

    allowed = await saml.should_allow_email_for_org(
        fake_session, uuid.uuid4(), "user@other.com"
    )

    assert allowed is False


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
    monkeypatch.setattr(
        saml, "_get_active_org_domains", AsyncMock(return_value={"acme.com"})
    )
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=False),
    )
    get_invitation_mock = AsyncMock(return_value=invitation)
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["invitee@acme.com"]
    )

    assert email == "invitee@acme.com"
    assert pending_invitation is invitation
    assert get_invitation_mock.await_count == 1


@pytest.mark.anyio
async def test_select_authorized_email_prefers_later_invited_candidate_over_first_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    invitation = SimpleNamespace(token="inv-123")
    monkeypatch.setattr(
        saml, "_get_active_org_domains", AsyncMock(return_value={"acme.com"})
    )
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=False),
    )
    get_invitation_mock = AsyncMock(side_effect=[None, invitation])
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session,
        uuid.uuid4(),
        ["primary@acme.com", "invitee@acme.com"],
    )

    assert email == "invitee@acme.com"
    assert pending_invitation is invitation
    assert get_invitation_mock.await_count == 2


@pytest.mark.anyio
async def test_select_authorized_email_falls_back_to_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    monkeypatch.setattr(
        saml, "_get_active_org_domains", AsyncMock(return_value={"example.com"})
    )
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        saml, "get_pending_org_invitation", AsyncMock(return_value=None)
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
    get_invitation_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        saml, "_get_active_org_domains", AsyncMock(return_value={"example.com"})
    )
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["user@other.com"]
    )

    assert email is None
    assert pending_invitation is None
    get_invitation_mock.assert_not_called()


@pytest.mark.anyio
async def test_select_authorized_email_rejects_invitation_without_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    invitation = SimpleNamespace(token="inv-123")
    get_invitation_mock = AsyncMock(return_value=invitation)
    monkeypatch.setattr(saml, "_get_active_org_domains", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["invitee@example.com"]
    )

    assert email is None
    assert pending_invitation is None
    get_invitation_mock.assert_not_called()


@pytest.mark.anyio
async def test_select_authorized_email_allows_superadmin_without_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    monkeypatch.setattr(saml, "_get_active_org_domains", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=True),
    )
    get_invitation_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["admin@example.com"]
    )

    assert email == "admin@example.com"
    assert pending_invitation is None
    get_invitation_mock.assert_not_called()


@pytest.mark.anyio
async def test_select_authorized_email_rejects_superadmin_without_domains_when_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    monkeypatch.setattr(saml, "_get_active_org_domains", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=False),
    )
    get_invitation_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        fake_session, uuid.uuid4(), ["admin@example.com"]
    )

    assert email is None
    assert pending_invitation is None
    get_invitation_mock.assert_not_called()


def test_should_allow_saml_user_auto_provisioning_for_first_superadmin() -> None:
    assert (
        saml.should_allow_saml_user_auto_provisioning(
            pending_invitation=None,
            is_first_superadmin_bootstrap=True,
        )
        is True
    )
    assert (
        saml.should_allow_saml_user_auto_provisioning(
            pending_invitation=None,
            is_first_superadmin_bootstrap=False,
        )
        is False
    )


def test_should_allow_saml_user_auto_provisioning_for_pending_invitation() -> None:
    invitation = cast(saml.OrganizationInvitation, SimpleNamespace(token="inv-123"))
    assert (
        saml.should_allow_saml_user_auto_provisioning(
            pending_invitation=invitation,
            is_first_superadmin_bootstrap=False,
        )
        is True
    )


@pytest.mark.anyio
async def test_is_first_superadmin_bootstrap_user_true_when_superadmin_and_no_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(saml, "TRACECAT__AUTH_SUPERADMIN_EMAIL", "admin@example.com")
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([0])

    allowed = await saml.is_first_superadmin_bootstrap_user(
        fake_session, "admin@example.com"
    )
    assert allowed is True


@pytest.mark.anyio
async def test_is_first_superadmin_bootstrap_user_false_when_users_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(saml, "TRACECAT__AUTH_SUPERADMIN_EMAIL", "admin@example.com")
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([2])

    allowed = await saml.is_first_superadmin_bootstrap_user(
        fake_session, "admin@example.com"
    )
    assert allowed is False


@pytest.mark.anyio
async def test_is_first_superadmin_bootstrap_user_false_for_non_superadmin_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(saml, "TRACECAT__AUTH_SUPERADMIN_EMAIL", "admin@example.com")
    fake_session = AsyncMock()

    allowed = await saml.is_first_superadmin_bootstrap_user(
        fake_session, "user@example.com"
    )
    assert allowed is False
    fake_session.execute.assert_not_called()


@pytest.mark.anyio
async def test_is_superadmin_saml_bootstrap_allowed_for_org_true_for_default_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    fake_session = AsyncMock()
    monkeypatch.setattr(
        saml, "is_first_superadmin_bootstrap_user", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        saml, "get_default_organization_id", AsyncMock(return_value=org_id)
    )

    allowed = await saml.is_superadmin_saml_bootstrap_allowed_for_org(
        fake_session, org_id, "admin@example.com"
    )

    assert allowed is True


@pytest.mark.anyio
async def test_is_superadmin_saml_bootstrap_allowed_for_org_false_for_non_default_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = AsyncMock()
    monkeypatch.setattr(
        saml, "is_first_superadmin_bootstrap_user", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        saml, "get_default_organization_id", AsyncMock(return_value=uuid.uuid4())
    )

    allowed = await saml.is_superadmin_saml_bootstrap_allowed_for_org(
        fake_session, uuid.uuid4(), "admin@example.com"
    )

    assert allowed is False


@pytest.mark.parametrize(
    (
        "has_existing_membership",
        "has_pending_invitation",
        "is_first_superadmin_bootstrap",
        "expected",
    ),
    [
        (False, False, False, False),
        (True, False, False, True),
        (False, True, False, True),
        (False, False, True, True),
        (True, True, False, True),
        (True, False, True, True),
        (False, True, True, True),
        (True, True, True, True),
    ],
)
def test_should_allow_saml_org_access_matrix(
    has_existing_membership: bool,
    has_pending_invitation: bool,
    is_first_superadmin_bootstrap: bool,
    expected: bool,
) -> None:
    pending_invitation = (
        cast(saml.OrganizationInvitation, SimpleNamespace(token="inv-123"))
        if has_pending_invitation
        else None
    )

    assert (
        saml.should_allow_saml_org_access(
            has_existing_membership=has_existing_membership,
            pending_invitation=pending_invitation,
            is_first_superadmin_bootstrap=is_first_superadmin_bootstrap,
        )
        is expected
    )
