from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from saml2.saml import assertion_from_string

from tracecat import config
from tracecat.auth import saml
from tracecat.auth.domain_policy import is_domain_allowed_for_org


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
    await_args = get_setting_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["default"] is None


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

    def scalar_one_or_none(self) -> object:
        return self._values[0] if self._values else None


def testis_domain_allowed_for_org_denies_no_domains_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", set())

    allowed = is_domain_allowed_for_org(
        normalized_domain="example.com", active_domains=set()
    )

    assert allowed is False


def testis_domain_allowed_for_org_allows_no_domains_in_single_tenant_when_env_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", set())

    allowed = is_domain_allowed_for_org(
        normalized_domain="example.com", active_domains=set()
    )

    assert allowed is True


def testis_domain_allowed_for_org_enforces_env_domains_in_single_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", {"acme.com"})

    allowed_matching = is_domain_allowed_for_org(
        normalized_domain="acme.com", active_domains=set()
    )
    allowed_non_matching = is_domain_allowed_for_org(
        normalized_domain="example.com", active_domains=set()
    )

    assert allowed_matching is True
    assert allowed_non_matching is False


def testis_domain_allowed_for_org_allows_allowlisted_domain() -> None:
    allowed = is_domain_allowed_for_org(
        normalized_domain="example.com", active_domains={"example.com"}
    )

    assert allowed is True


def testis_domain_allowed_for_org_denies_non_allowlisted_domain() -> None:
    allowed = is_domain_allowed_for_org(
        normalized_domain="other.com", active_domains={"example.com"}
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


_INVITATION = SimpleNamespace(token="inv-123")


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "domains",
        "multi_tenant",
        "superadmin_allowed",
        "invitations",
        "candidates",
        "expected_email",
        "expected_invitation_lookups",
    ),
    [
        pytest.param(
            {"acme.com"},
            None,
            False,
            [_INVITATION],
            ["invitee@acme.com"],
            "invitee@acme.com",
            1,
            id="prefers_pending_invitation",
        ),
        pytest.param(
            {"acme.com"},
            None,
            False,
            [None, _INVITATION],
            ["primary@acme.com", "invitee@acme.com"],
            "invitee@acme.com",
            2,
            id="prefers_later_invited_candidate_over_first_allowlisted",
        ),
        pytest.param(
            {"example.com"},
            None,
            False,
            [None],
            ["user@example.com"],
            "user@example.com",
            1,
            id="falls_back_to_allowlist",
        ),
        pytest.param(
            {"example.com"},
            None,
            False,
            [],
            ["user@other.com"],
            None,
            0,
            id="rejects_when_not_allowlisted_or_invited",
        ),
        pytest.param(
            set(),
            True,
            False,
            [_INVITATION],
            ["invitee@example.com"],
            None,
            0,
            id="rejects_invitation_without_domains",
        ),
        pytest.param(
            set(),
            False,
            False,
            [_INVITATION],
            ["invitee@example.com"],
            "invitee@example.com",
            1,
            id="allows_invitation_without_domains_in_single_tenant_when_env_not_set",
        ),
        pytest.param(
            set(),
            None,
            True,
            [],
            ["admin@example.com"],
            "admin@example.com",
            0,
            id="allows_superadmin_without_domains",
        ),
        pytest.param(
            set(),
            True,
            False,
            [],
            ["admin@example.com"],
            None,
            0,
            id="rejects_superadmin_without_domains_when_not_allowed",
        ),
    ],
)
async def test_select_authorized_email(
    monkeypatch: pytest.MonkeyPatch,
    domains: set[str],
    multi_tenant: bool | None,
    superadmin_allowed: bool,
    invitations: list[SimpleNamespace | None],
    candidates: list[str],
    expected_email: str | None,
    expected_invitation_lookups: int,
) -> None:
    if multi_tenant is not None:
        monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", multi_tenant)
        monkeypatch.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", set())
    monkeypatch.setattr(
        saml, "_get_active_org_domains", AsyncMock(return_value=domains)
    )
    monkeypatch.setattr(
        saml,
        "is_superadmin_saml_bootstrap_allowed_for_org",
        AsyncMock(return_value=superadmin_allowed),
    )
    get_invitation_mock = AsyncMock(side_effect=invitations)
    monkeypatch.setattr(saml, "get_pending_org_invitation", get_invitation_mock)

    email, pending_invitation = await saml._select_authorized_email(
        AsyncMock(), uuid.uuid4(), candidates
    )

    assert email == expected_email
    if expected_email is not None and invitations[-1:] == [_INVITATION]:
        assert pending_invitation is _INVITATION
    else:
        assert pending_invitation is None
    assert get_invitation_mock.await_count == expected_invitation_lookups


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
    invitation = cast(saml.Invitation, SimpleNamespace(token="inv-123"))
    assert (
        saml.should_allow_saml_user_auto_provisioning(
            pending_invitation=invitation,
            is_first_superadmin_bootstrap=False,
        )
        is True
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("email", "user_count", "expected"),
    [
        ("admin@example.com", 0, True),
        ("admin@example.com", 2, False),
        ("user@example.com", None, False),
    ],
    ids=["superadmin_and_no_users", "users_exist", "non_superadmin_email"],
)
async def test_is_first_superadmin_bootstrap_user(
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    user_count: int | None,
    expected: bool,
) -> None:
    monkeypatch.setattr(saml, "TRACECAT__AUTH_SUPERADMIN_EMAIL", "admin@example.com")
    fake_session = AsyncMock()
    fake_session.execute.return_value = _FakeResult([user_count])

    assert (
        await saml.is_first_superadmin_bootstrap_user(fake_session, email) is expected
    )
    if user_count is None:
        fake_session.execute.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("is_default_org", [True, False])
async def test_is_superadmin_saml_bootstrap_allowed_only_for_default_org(
    monkeypatch: pytest.MonkeyPatch, is_default_org: bool
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(
        saml, "is_first_superadmin_bootstrap_user", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        saml,
        "get_default_organization_id",
        AsyncMock(return_value=org_id if is_default_org else uuid.uuid4()),
    )

    allowed = await saml.is_superadmin_saml_bootstrap_allowed_for_org(
        AsyncMock(), org_id, "admin@example.com"
    )

    assert allowed is is_default_org


@pytest.mark.parametrize(
    (
        "has_existing_membership",
        "has_pending_invitation",
        "is_first_superadmin_bootstrap",
        "is_platform_superuser",
        "expected",
    ),
    [
        (False, False, False, False, False),
        (True, False, False, False, True),
        (False, True, False, False, True),
        (False, False, True, False, True),
        (False, False, False, True, False),
        (True, True, False, False, True),
        (True, False, True, False, True),
        (False, True, True, False, True),
        (True, True, True, False, True),
        (True, False, False, True, True),
        (False, True, False, True, True),
        (False, False, True, True, True),
        (True, True, False, True, True),
        (True, False, True, True, True),
        (False, True, True, True, True),
        (True, True, True, True, True),
    ],
)
def test_should_allow_saml_org_access_matrix(
    has_existing_membership: bool,
    has_pending_invitation: bool,
    is_first_superadmin_bootstrap: bool,
    is_platform_superuser: bool,
    expected: bool,
) -> None:
    pending_invitation = (
        cast(saml.Invitation, SimpleNamespace(token="inv-123"))
        if has_pending_invitation
        else None
    )

    assert (
        saml.should_allow_saml_org_access(
            has_existing_membership=has_existing_membership,
            pending_invitation=pending_invitation,
            is_first_superadmin_bootstrap=is_first_superadmin_bootstrap,
            is_platform_superuser=is_platform_superuser,
        )
        is expected
    )


def test_extract_candidate_emails_reads_parsed_assertion() -> None:
    assertion = assertion_from_string("""
        <saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
          <saml2:AttributeStatement>
            <saml2:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress">
              <saml2:AttributeValue>user@example.com</saml2:AttributeValue>
            </saml2:Attribute>
            <saml2:Attribute Name="email">
              <saml2:AttributeValue>primary@example.com</saml2:AttributeValue>
            </saml2:Attribute>
          </saml2:AttributeStatement>
        </saml2:Assertion>
    """)

    assert saml._extract_candidate_emails(saml.SAMLParser(assertion)) == [
        "primary@example.com",
        "user@example.com",
    ]


def test_saml_parser_rejects_assertion_without_attributes() -> None:
    assertion = assertion_from_string(
        '<saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"/>'
    )

    with pytest.raises(HTTPException) as exc_info:
        saml.SAMLParser(assertion).parse_to_dict()

    assert exc_info.value.status_code == 400
