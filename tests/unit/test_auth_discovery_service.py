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
from tracecat.db.models import Invitation, Organization, OrganizationDomain
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
    setting_reader.assert_any_await(
        "saml_enabled",
        organization_id=organization.id,
        session=session,
        default=True,
    )


def _settings_reader(values: dict[str, object]):
    async def _read(
        key: str, *, organization_id: uuid.UUID, session: object, default: object
    ) -> object:
        return values.get(key, default)

    return _read


@pytest.mark.anyio
async def test_discovery_org_hint_without_saml_setup_falls_back_to_oidc(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invite links carry an org hint; orgs that never set up SAML use OIDC."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    service = AuthDiscoveryService(session)

    response = await service.discover(
        "invitee@external-guest.example", org_slug=organization.slug
    )

    assert response.method == AuthDiscoveryMethod.OIDC
    assert response.next_url is None
    assert response.organization_slug == organization.slug


@pytest.mark.anyio
async def test_discovery_mapped_domain_without_saml_idp_falls_back_to_oidc(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create_domain(session, organization.id, "no-idp.com")
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        _settings_reader({"saml_enabled": True, "saml_idp_metadata_url": None}),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@no-idp.com")

    assert response.method == AuthDiscoveryMethod.OIDC


@pytest.mark.anyio
async def test_discovery_keeps_saml_when_enforced_without_idp(
    session: AsyncSession,
    organization: Organization,
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
        _settings_reader({"saml_enabled": True, "saml_enforced": True}),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover(
        "user@external-guest.example", org_slug=organization.slug
    )

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
async def test_discovery_uses_saml_when_org_idp_is_configured(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(config, "SAML_IDP_METADATA_URL", None)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        _settings_reader(
            {
                "saml_enabled": True,
                "saml_idp_metadata_url": "https://idp.example.com/metadata",
            }
        ),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover(
        "user@external-guest.example", org_slug=organization.slug
    )

    assert response.method == AuthDiscoveryMethod.SAML


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("multi_tenant", "expected"),
    [
        (False, AuthDiscoveryMethod.SAML),
        (True, AuthDiscoveryMethod.OIDC),
    ],
)
async def test_discovery_env_idp_metadata_only_applies_to_single_tenant(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
    multi_tenant: bool,
    expected: AuthDiscoveryMethod,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", multi_tenant)
    monkeypatch.setattr(
        config, "SAML_IDP_METADATA_URL", "https://idp.example.com/metadata"
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.OIDC, AuthType.SAML},
    )
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        _settings_reader({"saml_enabled": True}),
    )
    service = AuthDiscoveryService(session)

    response = await service.discover(
        "user@external-guest.example", org_slug=organization.slug
    )

    assert response.method == expected


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


@pytest.mark.anyio
async def test_discovery_ignores_invitations_once_saml_is_enabled(
    session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routing depends on org settings only, so it cannot reveal who is invited."""
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC, AuthType.SAML})
    monkeypatch.setattr(
        auth_discovery_module,
        "get_setting_from_bypass_session",
        AsyncMock(return_value=True),
    )
    await _create_domain(session, organization.id, "invite-saml.com")
    session.add(
        Invitation(
            id=uuid.uuid4(),
            organization_id=organization.id,
            email="invited@invite-saml.com",
            token=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            status=InvitationStatus.PENDING,
        )
    )
    await session.commit()
    service = AuthDiscoveryService(session)

    invited = await service.discover("invited@invite-saml.com")
    stranger = await service.discover("stranger@invite-saml.com")

    assert invited.method == stranger.method == AuthDiscoveryMethod.SAML
