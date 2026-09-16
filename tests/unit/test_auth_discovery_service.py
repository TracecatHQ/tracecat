"""Tests for pre-auth email domain discovery routing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth import discovery as auth_discovery_module
from tracecat.auth.discovery import AuthDiscoveryMethod, AuthDiscoveryService
from tracecat.auth.enums import AuthType
from tracecat.db.models import (
    Invitation,
    Organization,
    OrganizationDomain,
    Role,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.domains import normalize_domain

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def organization(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


async def _create_domain(
    session: AsyncSession, organization_id: uuid.UUID, domain: str
) -> OrganizationDomain:
    normalized = normalize_domain(domain)
    organization_domain = OrganizationDomain(
        id=uuid.uuid4(),
        organization_id=organization_id,
        domain=normalized.domain,
        normalized_domain=normalized.normalized_domain,
        is_primary=True,
        is_active=True,
        verification_method="platform_admin",
    )
    session.add(organization_domain)
    await session.commit()
    return organization_domain


@pytest.mark.anyio
async def test_discovery_prefers_saml_for_mapped_domains(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_domain(session, organization.id, "acme.com")
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    setting_reader = AsyncMock(return_value=True)
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        setting_reader,
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@acme.com")

    assert response.method == AuthDiscoveryMethod.SAML
    setting_reader.assert_awaited_once_with(
        "saml_enabled",
        organization_id=organization.id,
        session=session,
        default=True,
    )


@pytest.mark.anyio
async def test_discovery_returns_oidc_for_mapped_non_saml_domains(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_domain(session, organization.id, "acme.io")
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC},
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@acme.io")

    assert response.method == AuthDiscoveryMethod.OIDC


@pytest.mark.anyio
async def test_discovery_falls_back_when_mapped_org_is_inactive(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_domain(session, organization.id, "acme.dev")
    organization.is_active = False
    await session.commit()
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    service = AuthDiscoveryService(session)

    response = await service.discover("user@acme.dev")

    assert response.method == AuthDiscoveryMethod.OIDC


@pytest.mark.anyio
async def test_discovery_returns_safe_platform_fallback_for_unknown_domains(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC},
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@unknown-domain.example")

    assert response.method == AuthDiscoveryMethod.OIDC


@pytest.mark.anyio
async def test_discovery_prefers_default_org_saml_for_unknown_domains_in_single_tenant(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_default_organization_id",
        AsyncMock(return_value=organization.id),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@unknown-domain.example")

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
async def test_discovery_unknown_domains_fallback_to_oidc_in_multi_tenant_with_saml_enabled(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        AsyncMock(return_value=True),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@unknown-domain.example")

    assert response.method == AuthDiscoveryMethod.OIDC


@pytest.mark.anyio
async def test_discovery_returns_basic_when_basic_is_only_platform_auth_type(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC})
    service = AuthDiscoveryService(session)

    response = await service.discover("user@unknown-domain.example")

    assert response.method == AuthDiscoveryMethod.BASIC


@pytest.mark.anyio
async def test_discovery_prefers_org_hint_over_email_domain(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        AsyncMock(return_value=True),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover(
        "user@external-guest.example", org_slug=organization.slug
    )

    assert response.method == AuthDiscoveryMethod.SAML
    assert response.organization_slug == organization.slug
    assert response.next_url is not None
    assert f"org={organization.slug}" in response.next_url


@pytest.mark.anyio
async def test_discovery_rejects_invalid_org_hint_without_fallback(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_domain(session, organization.id, "acme.com")
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    service = AuthDiscoveryService(session)

    with pytest.raises(TracecatValidationError) as exc:
        await service.discover("user@acme.com", org_slug="does-not-exist")

    assert str(exc.value) == "Invalid organization"


async def _create_invitation(
    session: AsyncSession,
    organization_id: uuid.UUID,
    email: str,
    *,
    status: InvitationStatus = InvitationStatus.PENDING,
    expires_in: timedelta = timedelta(days=7),
) -> Invitation:
    role = Role(
        id=uuid.uuid4(),
        name="Organization member",
        slug=f"organization-member-{uuid.uuid4().hex[:8]}",
        organization_id=organization_id,
    )
    session.add(role)
    await session.flush()
    invitation = Invitation(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email=email,
        role_id=role.id,
        token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + expires_in,
        status=status,
    )
    session.add(invitation)
    await session.commit()
    return invitation


@pytest.fixture
def saml_org_auth_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        AsyncMock(return_value=True),
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("saml_org_auth_types")
async def test_discovery_offers_basic_for_pending_invitation_in_saml_org(
    session: AsyncSession,
    organization: Organization,
) -> None:
    await _create_domain(session, organization.id, "invite-basic.com")
    await _create_invitation(session, organization.id, "alice@invite-basic.com")
    service = AuthDiscoveryService(session)

    response = await service.discover("alice@invite-basic.com")

    assert response.method == AuthDiscoveryMethod.BASIC
    assert response.next_url is None


@pytest.mark.anyio
@pytest.mark.usefixtures("saml_org_auth_types")
async def test_discovery_keeps_saml_without_invitation(
    session: AsyncSession,
    organization: Organization,
) -> None:
    await _create_domain(session, organization.id, "no-invite.com")
    service = AuthDiscoveryService(session)

    response = await service.discover("bob@no-invite.com")

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
@pytest.mark.usefixtures("saml_org_auth_types")
async def test_discovery_keeps_saml_for_expired_invitation(
    session: AsyncSession,
    organization: Organization,
) -> None:
    await _create_domain(session, organization.id, "expired-invite.com")
    await _create_invitation(
        session,
        organization.id,
        "carol@expired-invite.com",
        expires_in=timedelta(days=-1),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("carol@expired-invite.com")

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
@pytest.mark.usefixtures("saml_org_auth_types")
async def test_discovery_keeps_saml_for_revoked_invitation(
    session: AsyncSession,
    organization: Organization,
) -> None:
    await _create_domain(session, organization.id, "revoked-invite.com")
    await _create_invitation(
        session,
        organization.id,
        "dave@revoked-invite.com",
        status=InvitationStatus.REVOKED,
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("dave@revoked-invite.com")

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
async def test_discovery_keeps_saml_when_basic_disabled(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_domain(session, organization.id, "basic-off.com")
    await _create_invitation(session, organization.id, "erin@basic-off.com")
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        AsyncMock(return_value=True),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("erin@basic-off.com")

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
@pytest.mark.usefixtures("saml_org_auth_types")
async def test_discovery_ignores_invitation_in_another_org(
    session: AsyncSession,
    organization: Organization,
) -> None:
    other_org = Organization(
        id=uuid.uuid4(),
        name="Other",
        slug=f"other-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.commit()
    await _create_domain(session, organization.id, "cross-org.com")
    await _create_invitation(session, other_org.id, "frank@cross-org.com")
    service = AuthDiscoveryService(session)

    response = await service.discover("frank@cross-org.com")

    assert response.method == AuthDiscoveryMethod.SAML
