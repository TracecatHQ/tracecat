"""Tests for pre-auth email domain discovery routing."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.discovery import AuthDiscoveryMethod, AuthDiscoveryService
from tracecat.auth.enums import AuthType
from tracecat.db.models import Organization, OrganizationDomain
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
    service = AuthDiscoveryService(session)

    response = await service.discover("user@acme.dev")

    assert response.method == AuthDiscoveryMethod.OIDC


@pytest.mark.anyio
async def test_discovery_returns_safe_platform_fallback_for_unknown_domains(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.GOOGLE_OAUTH},
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
