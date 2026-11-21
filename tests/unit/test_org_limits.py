"""Tests for per-organization concurrency limits and the limits read path."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import Organization, OrganizationTier, Tier
from tracecat.exceptions import ScopeDeniedError
from tracecat.identifiers import OrganizationID
from tracecat.tiers import service as tier_service_module
from tracecat.tiers.enums import ConcurrencyLimitName
from tracecat.tiers.schemas import (
    EffectiveLimits,
    OrganizationTierUpdate,
    TierCreate,
    TierUpdate,
)
from tracecat.tiers.service import OrgLimitsService, TierService
from tracecat.tiers.types import ConcurrencyUsage

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def enable_multi_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)


@pytest.fixture
async def test_org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name=f"Limits Test Org {uuid.uuid4().hex[:8]}",
        slug=f"limits-test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


def _org_role(org_id: uuid.UUID, scopes: frozenset[str]) -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        organization_id=org_id,
        user_id=uuid.uuid4(),
        scopes=scopes,
    )


async def _assign_tier(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    tier_tool_calls: int | None,
    tier_agent_turns: int | None,
    override_tool_calls: int | None,
    override_agent_turns: int | None,
) -> None:
    tier = Tier(
        display_name=f"Tier {uuid.uuid4().hex[:8]}",
        max_concurrent_actions=8,
        max_concurrent_tool_calls=tier_tool_calls,
        max_concurrent_agent_turns=tier_agent_turns,
        entitlements={},
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
            max_concurrent_tool_calls=override_tool_calls,
            max_concurrent_agent_turns=override_agent_turns,
        )
    )
    await session.commit()


@pytest.mark.anyio
async def test_new_limits_inherit_tier_unless_overridden(
    session: AsyncSession, test_org: Organization
) -> None:
    """A null override inherits the tier value; a set override wins."""
    await _assign_tier(
        session,
        test_org.id,
        tier_tool_calls=5,
        tier_agent_turns=2,
        override_tool_calls=None,
        override_agent_turns=3,
    )

    limits = await TierService(session).get_effective_limits(test_org.id)

    assert limits.max_concurrent_tool_calls == 5
    assert limits.max_concurrent_agent_turns == 3


@pytest.mark.anyio
async def test_get_limits_reports_unknown_usage(
    session: AsyncSession,
    test_org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every limit is listed; without enforcement usage is unknown, never zero."""
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())
    await _assign_tier(
        session,
        test_org.id,
        tier_tool_calls=None,
        tier_agent_turns=4,
        override_tool_calls=None,
        override_agent_turns=None,
    )
    role = _org_role(test_org.id, frozenset({"org:read"}))

    result = await OrgLimitsService(session, role=role).get_limits()

    by_name = {entry.name: entry for entry in result.concurrency}
    assert list(by_name) == list(ConcurrencyLimitName)
    assert by_name[ConcurrencyLimitName.MAX_CONCURRENT_WORKFLOWS].limit is None
    assert by_name[ConcurrencyLimitName.MAX_CONCURRENT_ACTIONS].limit == 8
    assert by_name[ConcurrencyLimitName.MAX_CONCURRENT_TOOL_CALLS].limit is None
    assert by_name[ConcurrencyLimitName.MAX_CONCURRENT_AGENT_TURNS].limit == 4
    for entry in result.concurrency:
        assert entry.in_use is None
        assert entry.observed_at is None


@pytest.mark.anyio
async def test_get_limits_reports_observed_usage(
    session: AsyncSession,
    test_org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Usage that was read is reported with its freshness."""
    await _assign_tier(
        session,
        test_org.id,
        tier_tool_calls=None,
        tier_agent_turns=None,
        override_tool_calls=None,
        override_agent_turns=None,
    )
    observed_at = datetime(2026, 11, 20, tzinfo=UTC)

    async def _read_usage(
        org_id: OrganizationID,
        limits: EffectiveLimits,
    ) -> dict[ConcurrencyLimitName, ConcurrencyUsage]:
        assert org_id == test_org.id
        assert limits.max_concurrent_tool_calls is None
        return {
            ConcurrencyLimitName.MAX_CONCURRENT_ACTIONS: ConcurrencyUsage(
                in_use=3, observed_at=observed_at
            )
        }

    monkeypatch.setattr(tier_service_module, "read_concurrency_usage", _read_usage)
    role = _org_role(test_org.id, frozenset({"org:read"}))

    result = await OrgLimitsService(session, role=role).get_limits()

    by_name = {entry.name: entry for entry in result.concurrency}
    actions = by_name[ConcurrencyLimitName.MAX_CONCURRENT_ACTIONS]
    assert actions.in_use == 3
    assert actions.observed_at == observed_at
    assert by_name[ConcurrencyLimitName.MAX_CONCURRENT_TOOL_CALLS].in_use is None


@pytest.mark.anyio
async def test_get_limits_single_tenant_is_unlimited(
    session: AsyncSession,
    test_org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    role = _org_role(test_org.id, frozenset({"org:read"}))

    result = await OrgLimitsService(session, role=role).get_limits()

    assert [entry.limit for entry in result.concurrency] == [None] * len(
        ConcurrencyLimitName
    )


@pytest.mark.anyio
async def test_get_limits_requires_org_read(
    session: AsyncSession, test_org: Organization
) -> None:
    role = _org_role(test_org.id, frozenset({"org:member:read"}))

    with pytest.raises(ScopeDeniedError):
        await OrgLimitsService(session, role=role).get_limits()


@pytest.mark.parametrize("value", [0, -1])
@pytest.mark.parametrize(
    "field",
    [
        "max_concurrent_workflows",
        "max_concurrent_actions",
        "max_concurrent_tool_calls",
        "max_concurrent_agent_turns",
    ],
)
def test_write_schemas_reject_non_positive_concurrency_limits(
    field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        TierCreate.model_validate({"display_name": "Tier", field: value})
    with pytest.raises(ValidationError):
        TierUpdate.model_validate({field: value})
    with pytest.raises(ValidationError):
        OrganizationTierUpdate.model_validate({field: value})


def test_override_null_clears_to_inherit() -> None:
    """An explicit null stays set, so the admin service clears the override."""
    update = OrganizationTierUpdate.model_validate({"max_concurrent_tool_calls": None})

    assert update.model_dump(exclude_unset=True) == {"max_concurrent_tool_calls": None}
