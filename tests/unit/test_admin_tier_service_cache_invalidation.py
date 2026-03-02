from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.tiers.service import AdminTierService

from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.tiers.schemas import OrganizationTierUpdate, TierUpdate

pytestmark = pytest.mark.usefixtures("db")


def _tier(*, is_default: bool) -> Tier:
    return Tier(
        display_name=f"Tier {uuid.uuid4().hex[:8]}",
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=None,
        api_rate_limit=None,
        api_burst_capacity=None,
        entitlements={},
        is_default=is_default,
        sort_order=0,
        is_active=True,
    )


@pytest.mark.anyio
async def test_update_org_tier_invalidates_effective_limits_cache(
    session: AsyncSession,
    test_admin_role: Role,
) -> None:
    org_id = test_admin_role.organization_id
    assert org_id is not None
    org = Organization(
        id=org_id,
        name=f"Org {uuid.uuid4().hex[:8]}",
        slug=f"org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    default_tier = _tier(is_default=True)
    session.add_all([org, default_tier])
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    with patch(
        "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache",
        new=AsyncMock(),
    ) as invalidate_mock:
        result = await service.update_org_tier(
            org_id,
            OrganizationTierUpdate(max_concurrent_actions=4),
        )

    assert result.max_concurrent_actions == 4
    invalidate_mock.assert_awaited_once_with(org_id)


@pytest.mark.anyio
async def test_update_tier_invalidates_assigned_organization_caches(
    session: AsyncSession,
    test_admin_role: Role,
) -> None:
    org_a_id = test_admin_role.organization_id
    assert org_a_id is not None
    org_a = Organization(
        id=org_a_id,
        name=f"Org {uuid.uuid4().hex[:8]}",
        slug=f"org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    shared_tier = _tier(is_default=False)
    org_b = Organization(
        id=uuid.uuid4(),
        name=f"Org {uuid.uuid4().hex[:8]}",
        slug=f"org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([org_a, shared_tier, org_b])
    await session.flush()
    session.add_all(
        [
            OrganizationTier(
                organization_id=org_a_id,
                tier_id=shared_tier.id,
            ),
            OrganizationTier(
                organization_id=org_b.id,
                tier_id=shared_tier.id,
            ),
        ]
    )
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    with patch(
        "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache_many",
        new=AsyncMock(),
    ) as invalidate_many_mock:
        await service.update_tier(
            shared_tier.id,
            TierUpdate(
                display_name=shared_tier.display_name,
                max_concurrent_actions=7,
            ),
        )

    invalidate_many_mock.assert_awaited_once()
    assert invalidate_many_mock.await_args is not None
    invalidated_org_ids = set(invalidate_many_mock.await_args.args[0])
    assert invalidated_org_ids == {org_a_id, org_b.id}


@pytest.mark.anyio
async def test_update_tier_explicit_null_clears_limit(
    session: AsyncSession,
    test_admin_role: Role,
) -> None:
    tier = _tier(is_default=False)
    tier.max_concurrent_workflows = 5
    session.add(tier)
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    result = await service.update_tier(
        tier.id,
        TierUpdate(
            display_name=tier.display_name,
            max_concurrent_workflows=None,
        ),
    )

    assert result.max_concurrent_workflows is None

    await session.refresh(tier)
    assert tier.max_concurrent_workflows is None
