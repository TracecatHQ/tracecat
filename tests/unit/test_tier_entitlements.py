"""Tests for tier-based entitlement resolution and enforcement."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.exceptions import EntitlementRequired
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.entitlements import Entitlement, EntitlementService
from tracecat.tiers.service import TierService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def enable_multi_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this suite in multi-tenant mode unless explicitly overridden."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)


@pytest.fixture
async def test_org(session: AsyncSession) -> Organization:
    """Create an organization for tier entitlement tests."""
    org = Organization(
        id=uuid.uuid4(),
        name=f"Tier Test Org {uuid.uuid4().hex[:8]}",
        slug=f"tier-test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


async def _create_org_tier(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    entitlements: dict[str, bool],
    entitlement_overrides: dict[str, bool] | None = None,
) -> Tier:
    """Create and assign a tier to an organization."""
    tier = Tier(
        display_name=f"Tier {uuid.uuid4().hex[:8]}",
        entitlements=entitlements,
        is_default=False,
        sort_order=0,
        is_active=True,
    )
    session.add(tier)
    await session.flush()

    session.add(
        OrganizationTier(
            organization_id=org_id,
            tier_id=tier.id,
            entitlement_overrides=entitlement_overrides,
        )
    )
    await session.commit()
    return tier


async def _create_default_tier(
    session: AsyncSession,
    *,
    entitlements: dict[str, bool],
) -> Tier:
    """Create an active default tier."""
    tier = Tier(
        display_name=f"Default Tier {uuid.uuid4().hex[:8]}",
        entitlements=entitlements,
        is_default=True,
        sort_order=0,
        is_active=True,
    )
    session.add(tier)
    await session.commit()
    return tier


@pytest.mark.anyio
async def test_specific_tier_entitlements_drive_effective_values(
    session: AsyncSession, test_org: Organization
) -> None:
    """Assigned tier entitlements should directly control effective values."""
    await _create_org_tier(
        session,
        test_org.id,
        entitlements={
            "case_addons": True,
            "agent_addons": False,
            "git_sync": True,
        },
    )

    tier_service = TierService(session)
    effective = await tier_service.get_effective_entitlements(test_org.id)

    assert effective.case_addons is True
    assert effective.agent_addons is False
    assert effective.git_sync is True


@pytest.mark.anyio
async def test_org_entitlement_overrides_take_precedence_over_tier(
    session: AsyncSession, test_org: Organization
) -> None:
    """Per-org entitlement overrides should win over the assigned tier values."""
    await _create_org_tier(
        session,
        test_org.id,
        entitlements={"case_addons": True, "agent_addons": True},
        entitlement_overrides={"case_addons": False, "agent_addons": False},
    )

    tier_service = TierService(session)
    effective = await tier_service.get_effective_entitlements(test_org.id)

    assert effective.case_addons is False
    assert effective.agent_addons is False


@pytest.mark.anyio
async def test_entitlement_check_changes_when_tier_entitlement_is_updated(
    session: AsyncSession, test_org: Organization
) -> None:
    """Changing a tier entitlement should flip entitlement checks for that org."""
    tier = await _create_org_tier(
        session,
        test_org.id,
        entitlements={"case_addons": False},
    )
    entitlement_service = EntitlementService(TierService(session))

    with pytest.raises(EntitlementRequired):
        await entitlement_service.check_entitlement(
            test_org.id, Entitlement.CASE_ADDONS
        )

    tier.entitlements = {**tier.entitlements, "case_addons": True}
    await session.commit()

    await entitlement_service.check_entitlement(test_org.id, Entitlement.CASE_ADDONS)


@pytest.mark.anyio
async def test_entitlement_without_org_tier_uses_default_tier(
    session: AsyncSession, test_org: Organization
) -> None:
    """When org tier is missing, entitlement checks use DB default tier."""
    await _create_default_tier(
        session,
        entitlements={"case_addons": True},
    )
    entitlement_service = EntitlementService(TierService(session))

    await entitlement_service.check_entitlement(test_org.id, Entitlement.CASE_ADDONS)


@pytest.mark.anyio
async def test_entitlement_without_org_tier_does_not_use_self_host_defaults(
    session: AsyncSession, test_org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-host DEFAULT_ENTITLEMENTS must not bypass DB default tier settings."""
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"case_addons": True}),
    )
    await _create_default_tier(
        session,
        entitlements={"case_addons": False},
    )
    entitlement_service = EntitlementService(TierService(session))

    with pytest.raises(EntitlementRequired):
        await entitlement_service.check_entitlement(
            test_org.id, Entitlement.CASE_ADDONS
        )


@pytest.mark.anyio
async def test_single_tenant_effective_values_use_static_defaults(
    session: AsyncSession, test_org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-tenant mode should return static self-host defaults."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    await _create_org_tier(
        session,
        test_org.id,
        entitlements={"case_addons": False, "agent_addons": False},
        entitlement_overrides={"case_addons": False, "agent_addons": False},
    )

    tier_service = TierService(session)
    effective_limits = await tier_service.get_effective_limits(test_org.id)
    effective_entitlements = await tier_service.get_effective_entitlements(test_org.id)

    assert effective_limits.model_dump() == tier_defaults.DEFAULT_LIMITS.model_dump()
    assert (
        effective_entitlements.model_dump()
        == tier_defaults.DEFAULT_ENTITLEMENTS.model_dump()
    )


@pytest.mark.anyio
async def test_single_tenant_entitlement_check_uses_static_defaults(
    session: AsyncSession, test_org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-tenant entitlement checks should not require DB tier rows."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    entitlement_service = EntitlementService(TierService(session))

    await entitlement_service.check_entitlement(test_org.id, Entitlement.CASE_ADDONS)
