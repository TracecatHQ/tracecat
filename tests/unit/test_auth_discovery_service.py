"""Tests for pre-auth email domain discovery routing."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth import discovery as auth_discovery_module
from tracecat.auth.discovery import AuthDiscoveryMethod, AuthDiscoveryService
from tracecat.auth.enums import AuthType
from tracecat.db.models import Organization, OrganizationDomain
from tracecat.exceptions import TracecatValidationError
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH, AuthType.SAML},
    )
    service = AuthDiscoveryService(session)

    response = await service.discover("user@acme.com")

    assert response.method == AuthDiscoveryMethod.SAML


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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH},
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH, AuthType.SAML},
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH},
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH, AuthType.SAML},
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH, AuthType.SAML},
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH, AuthType.SAML},
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
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH, AuthType.SAML},
    )
    service = AuthDiscoveryService(session)

    with pytest.raises(TracecatValidationError) as exc:
        await service.discover("user@acme.com", org_slug="does-not-exist")

    assert str(exc.value) == "Invalid organization"
