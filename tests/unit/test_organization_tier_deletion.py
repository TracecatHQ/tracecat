"""Organization deletion must remove its tier assignment, preserving peer orgs."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.organization.management import delete_organization_with_cleanup


@pytest.mark.anyio
@pytest.mark.parametrize("loaded", [False, True])
async def test_delete_organization_with_tier(
    session: AsyncSession, loaded: bool
) -> None:
    tier = Tier(id=uuid.uuid4(), display_name="Deletion test tier")
    org = Organization(
        id=uuid.uuid4(), name="Delete test", slug=f"delete-{uuid.uuid4().hex}"
    )
    peer = Organization(
        id=uuid.uuid4(), name="Peer test", slug=f"peer-{uuid.uuid4().hex}"
    )
    session.add_all([tier, org, peer])
    await session.flush()
    session.add_all(
        [
            OrganizationTier(organization_id=org.id, tier_id=tier.id),
            OrganizationTier(organization_id=peer.id, tier_id=tier.id),
        ]
    )
    await session.flush()
    if loaded:
        await session.refresh(org, ["organization_tier"])
    else:
        session.expire(org, ["organization_tier"])
    await delete_organization_with_cleanup(
        session, organization=org, operator_user_id=None
    )
    await session.flush()
    remaining = (
        (
            await session.execute(
                select(OrganizationTier.organization_id).where(
                    OrganizationTier.tier_id == tier.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert remaining == [peer.id]
    assert await session.get(Tier, tier.id) is not None
