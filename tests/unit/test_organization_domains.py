"""Tests for organization domain normalization and constraints."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Organization, OrganizationDomain
from tracecat.organization.domains import normalize_domain

pytestmark = pytest.mark.usefixtures("db")


def test_normalize_domain_trims_lowercases_and_punycodes() -> None:
    normalized = normalize_domain("  B\u00dcCHER.Example. ")
    assert normalized.domain == "b\u00fccher.example"
    assert normalized.normalized_domain == "xn--bcher-kva.example"


def test_normalize_domain_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="Domain cannot be empty"):
        normalize_domain("   ")

    with pytest.raises(ValueError, match="Domain cannot be empty"):
        normalize_domain(".")


@pytest.mark.anyio
async def test_active_domain_is_globally_unique(session: AsyncSession) -> None:
    org_a = Organization(
        id=uuid.uuid4(),
        name="Org A",
        slug=f"org-a-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    org_b = Organization(
        id=uuid.uuid4(),
        name="Org B",
        slug=f"org-b-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([org_a, org_b])
    await session.commit()

    first = normalize_domain("acme.com")
    second = normalize_domain("acme.com.")
    session.add(
        OrganizationDomain(
            organization_id=org_a.id,
            domain=first.domain,
            normalized_domain=first.normalized_domain,
            is_primary=True,
            is_active=True,
        )
    )
    await session.commit()

    session.add(
        OrganizationDomain(
            organization_id=org_b.id,
            domain=second.domain,
            normalized_domain=second.normalized_domain,
            is_primary=False,
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.anyio
async def test_inactive_duplicate_domain_is_allowed(session: AsyncSession) -> None:
    org_a = Organization(
        id=uuid.uuid4(),
        name="Org A",
        slug=f"org-a-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    org_b = Organization(
        id=uuid.uuid4(),
        name="Org B",
        slug=f"org-b-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([org_a, org_b])
    await session.commit()

    values = normalize_domain("example.org")
    session.add(
        OrganizationDomain(
            organization_id=org_a.id,
            domain=values.domain,
            normalized_domain=values.normalized_domain,
            is_primary=True,
            is_active=True,
        )
    )
    await session.commit()

    session.add(
        OrganizationDomain(
            organization_id=org_b.id,
            domain=values.domain,
            normalized_domain=values.normalized_domain,
            is_primary=False,
            is_active=False,
        )
    )
    await session.commit()


@pytest.mark.anyio
async def test_org_can_only_have_one_active_primary_domain(
    session: AsyncSession,
) -> None:
    org = Organization(
        id=uuid.uuid4(),
        name="Org A",
        slug=f"org-a-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()

    first = normalize_domain("one.example")
    second = normalize_domain("two.example")
    session.add(
        OrganizationDomain(
            organization_id=org.id,
            domain=first.domain,
            normalized_domain=first.normalized_domain,
            is_primary=True,
            is_active=True,
        )
    )
    await session.commit()

    session.add(
        OrganizationDomain(
            organization_id=org.id,
            domain=second.domain,
            normalized_domain=second.normalized_domain,
            is_primary=True,
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
