"""Isolated activation preview regressions with directory reads stubbed."""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from tracecat_ee.scim.schemas import ExternalGroupMappingCreate
from tracecat_ee.scim.service import SCIMService

from tracecat.auth.types import Role
from tracecat.db.models import ExternalGroup


@pytest.mark.parametrize("reverse", [False, True])
def test_activation_review_combines_sources_per_target(
    monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    """Retained users stay, shared gains count once, and targets stay separate."""
    organization_id, target, other_target = (UUID(int=n) for n in range(1, 4))
    source_a, source_b, source_c = (UUID(int=n) for n in range(4, 7))
    retained, lost, shared, gain_a, gain_b, existing = (
        UUID(int=n) for n in range(10, 16)
    )
    manual_emails = {retained: "retained@example.com", lost: "lost@example.com"}
    members = {
        source_a: {shared, gain_a, existing},
        source_b: {retained, shared, gain_b, existing},
        source_c: set(),
    }
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "Target"
    result.tuples.return_value.all.return_value = list(manual_emails.items())
    session.execute.return_value = result
    service = SCIMService(
        session,
        Role(
            type="service", organization_id=organization_id, service_id="tracecat-api"
        ),
    )
    monkeypatch.setattr(service, "_directory_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_get_external_group",
        AsyncMock(
            side_effect=lambda group_id: ExternalGroup(
                id=group_id, organization_id=organization_id, display_name="Source"
            )
        ),
    )
    monkeypatch.setattr(
        service, "_external_group_user_ids", AsyncMock(side_effect=members.__getitem__)
    )
    monkeypatch.setattr(service, "_idp_member_ids", AsyncMock(return_value={existing}))
    sources = [source_b, source_a] if reverse else [source_a, source_b]
    proposed = [
        ExternalGroupMappingCreate(external_group_id=sources[0], group_id=target),
        ExternalGroupMappingCreate(external_group_id=source_c, group_id=other_target),
        ExternalGroupMappingCreate(external_group_id=sources[1], group_id=target),
        ExternalGroupMappingCreate(external_group_id=sources[0], group_id=target),
    ]

    review = asyncio.run(
        inspect.unwrap(SCIMService.review_activation)(service, proposed)
    )

    assert [(p.external_group_id, p.group_id) for p in review.plans] == [
        (p.external_group_id, p.group_id) for p in proposed
    ]
    combined, other, remaining, duplicate = review.plans
    assert set(combined.manual_members_purged) == {retained, lost}
    assert combined.manual_member_emails == manual_emails
    assert set(combined.users_gaining_access) == {shared, gain_a, gain_b}
    assert combined.users_losing_access == [lost]
    assert set(other.manual_members_purged) == {retained, lost}
    assert set(other.users_losing_access) == {retained, lost}
    assert other.users_gaining_access == []
    for plan in (remaining, duplicate):
        assert plan.manual_members_purged == []
        assert plan.manual_member_emails == {}
        assert plan.users_gaining_access == []
        assert plan.users_losing_access == []
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
