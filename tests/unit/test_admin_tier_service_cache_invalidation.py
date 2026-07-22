from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.tiers.service import AdminTierService

from tracecat import config
from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.schemas import OrganizationTierUpdate, TierCreate, TierUpdate

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def enable_multi_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)


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
async def test_create_default_tier_queues_newly_entitled_default_follower(
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
        "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
        new=AsyncMock(),
    ) as enqueue_mock:
        await service.create_tier(
            TierCreate(
                display_name=f"Tier {uuid.uuid4().hex[:8]}",
                entitlements={"case_addons": True},
                is_default=True,
            )
        )

    enqueue_mock.assert_any_await(org_id)


@pytest.mark.anyio
async def test_create_non_default_tier_does_not_queue_backfill(
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
        "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
        new=AsyncMock(),
    ) as enqueue_mock:
        await service.create_tier(
            TierCreate(
                display_name=f"Tier {uuid.uuid4().hex[:8]}",
                entitlements={"case_addons": True},
                is_default=False,
            )
        )

    enqueue_mock.assert_not_awaited()


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
async def test_update_org_tier_completes_in_single_tenant_mode(
    session: AsyncSession,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
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
    ):
        result = await service.update_org_tier(
            org_id,
            OrganizationTierUpdate(max_concurrent_actions=4),
        )

    assert result.organization_id == org_id
    assert result.max_concurrent_actions == 4


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
    org_b_id = org_b.id
    session.add_all([org_a, shared_tier, org_b])
    await session.flush()
    session.add_all(
        [
            OrganizationTier(
                organization_id=org_a_id,
                tier_id=shared_tier.id,
            ),
            OrganizationTier(
                organization_id=org_b_id,
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
    assert invalidated_org_ids == {org_a_id, org_b_id}


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


@pytest.mark.anyio
async def test_update_org_tier_grant_queues_case_duration_backfill(
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

    with (
        patch(
            "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache",
            new=AsyncMock(),
        ),
        patch(
            "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        await service.update_org_tier(
            org_id,
            OrganizationTierUpdate(entitlement_overrides={"case_addons": True}),
        )

    enqueue_mock.assert_awaited_once_with(org_id)


@pytest.mark.anyio
async def test_update_org_tier_tier_id_grant_queues_case_duration_backfill(
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
    tier_without_case_addons = _tier(is_default=True)
    tier_with_case_addons = _tier(is_default=False)
    tier_with_case_addons.entitlements = {"case_addons": True}
    session.add_all([org, tier_without_case_addons, tier_with_case_addons])
    await session.flush()
    session.add(
        OrganizationTier(
            organization_id=org_id,
            tier_id=tier_without_case_addons.id,
        )
    )
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    with (
        patch(
            "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache",
            new=AsyncMock(),
        ),
        patch(
            "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        result = await service.update_org_tier(
            org_id,
            OrganizationTierUpdate(tier_id=tier_with_case_addons.id),
        )

    enqueue_mock.assert_awaited_once_with(org_id)
    assert result.tier_id == tier_with_case_addons.id
    assert result.tier is not None
    assert result.tier.id == tier_with_case_addons.id


@pytest.mark.anyio
async def test_update_org_tier_without_effective_change_skips_duration_backfill(
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

    with (
        patch(
            "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache",
            new=AsyncMock(),
        ),
        patch(
            "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        await service.update_org_tier(
            org_id,
            OrganizationTierUpdate(max_concurrent_actions=4),
        )

    enqueue_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_update_tier_grant_queues_only_newly_entitled_org(
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
    org_b = Organization(
        id=uuid.uuid4(),
        name=f"Org {uuid.uuid4().hex[:8]}",
        slug=f"org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    default_tier = _tier(is_default=True)
    tier = _tier(is_default=False)
    session.add_all([org_a, org_b, default_tier, tier])
    await session.flush()
    session.add_all(
        [
            OrganizationTier(organization_id=org_a_id, tier_id=tier.id),
            OrganizationTier(
                organization_id=org_b.id,
                tier_id=tier.id,
                entitlement_overrides={"case_addons": True},
            ),
        ]
    )
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    with (
        patch(
            "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache_many",
            new=AsyncMock(),
        ),
        patch(
            "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        await service.update_tier(
            tier.id,
            TierUpdate(
                display_name=tier.display_name,
                entitlements={"case_addons": True},
            ),
        )

    enqueue_mock.assert_awaited_once_with(org_a_id)


@pytest.mark.anyio
async def test_update_tier_activation_queues_newly_entitled_default_follower(
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
    default_tier.is_active = False
    default_tier.entitlements = {"case_addons": True}
    session.add_all([org, default_tier])
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    with patch(
        "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
        new=AsyncMock(),
    ) as enqueue_mock:
        await service.update_tier(
            default_tier.id,
            TierUpdate(display_name=default_tier.display_name, is_active=True),
        )

    # Other default-follower orgs in the test database (e.g. the platform
    # org) legitimately flip too; only pin the org this test created.
    enqueue_mock.assert_any_await(org_id)


@pytest.mark.anyio
async def test_case_addons_entitled_org_ids_resolves_overrides_and_tiers(
    session: AsyncSession,
    test_admin_role: Role,
) -> None:
    override_org_id = test_admin_role.organization_id
    assert override_org_id is not None
    assigned_org_id = uuid.uuid4()
    follower_org_id = uuid.uuid4()
    orgs = [
        Organization(
            id=org_id,
            name=f"Org {uuid.uuid4().hex[:8]}",
            slug=f"org-{uuid.uuid4().hex[:8]}",
            is_active=True,
        )
        for org_id in (override_org_id, assigned_org_id, follower_org_id)
    ]
    default_tier = _tier(is_default=True)
    default_tier.entitlements = {"case_addons": True}
    granting_tier = _tier(is_default=False)
    granting_tier.entitlements = {"case_addons": True}
    denying_tier = _tier(is_default=False)
    session.add_all([*orgs, default_tier, granting_tier, denying_tier])
    await session.flush()
    session.add_all(
        [
            OrganizationTier(
                organization_id=override_org_id,
                tier_id=denying_tier.id,
                entitlement_overrides={"case_addons": True},
            ),
            OrganizationTier(
                organization_id=assigned_org_id,
                tier_id=granting_tier.id,
            ),
        ]
    )
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    entitled_org_ids = await service._case_addons_entitled_org_ids(
        [override_org_id, assigned_org_id, follower_org_id]
    )

    assert entitled_org_ids == {
        override_org_id,
        assigned_org_id,
        follower_org_id,
    }


@pytest.mark.anyio
async def test_case_addons_entitled_org_ids_requires_active_default(
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
    inactive_default_tier = _tier(is_default=True)
    inactive_default_tier.is_active = False
    inactive_default_tier.entitlements = {"case_addons": True}
    session.add_all([org, inactive_default_tier])
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    assert await service._case_addons_entitled_org_ids([org_id]) == set()


@pytest.mark.anyio
async def test_case_addons_entitled_org_ids_uses_single_tenant_defaults(
    session: AsyncSession,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"case_addons": True}),
    )
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    assert await service._case_addons_entitled_org_ids(org_ids) == set(org_ids)


@pytest.mark.anyio
async def test_update_tier_clears_sole_default_without_backfill_error(
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
    default_tier.entitlements = {"case_addons": True}
    session.add_all([org, default_tier])
    await session.commit()

    service = AdminTierService(session, cast(PlatformRole, test_admin_role))

    with patch(
        "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
        new=AsyncMock(),
    ) as enqueue_mock:
        result = await service.update_tier(
            default_tier.id,
            TierUpdate(
                display_name=default_tier.display_name,
                is_default=False,
            ),
        )

    assert result.is_default is False
    enqueue_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_case_duration_backfill_failure_does_not_fail_org_tier_update(
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

    with (
        patch(
            "tracecat_ee.admin.tiers.service.invalidate_effective_limits_cache",
            new=AsyncMock(),
        ),
        patch(
            "tracecat_ee.admin.tiers.service.enqueue_case_duration_backfill_for_org",
            new=AsyncMock(side_effect=RuntimeError("queue unavailable")),
        ) as enqueue_mock,
        patch("tracecat_ee.admin.tiers.service.logger.warning") as warning_mock,
    ):
        result = await service.update_org_tier(
            org_id,
            OrganizationTierUpdate(entitlement_overrides={"case_addons": True}),
        )

    assert result.entitlement_overrides == {"case_addons": True}
    enqueue_mock.assert_awaited_once_with(org_id)
    warning_mock.assert_called_once()
