"""Isolated regressions for shared, set-oriented SCIM admission."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from tracecat_ee.scim.service import SCIMService

from tracecat.auth.types import Role


@pytest.mark.parametrize("user_count", [0, 1, 100])
def test_admit_pushed_users_uses_bounded_org_scoped_queries(user_count: int) -> None:
    """Activation must not load the active cohort or execute SQL per user."""
    organization_id = UUID(int=1)
    session = AsyncMock()
    inactive = MagicMock()
    inactive.all.return_value = []
    active = MagicMock()
    active.all.return_value = [UUID(int=n + 2) for n in range(user_count)]
    # The second result is available to catch a regression to loading active IDs.
    session.scalars.side_effect = [inactive, active]
    session.scalar.return_value = "member@example.com"
    service = SCIMService(
        session,
        Role(
            type="service", organization_id=organization_id, service_id="tracecat-api"
        ),
    )

    asyncio.run(service._admit_pushed_users())

    assert (
        session.scalars.await_count == 1
    )  # Inactive users still deprovision individually.
    session.scalar.assert_not_awaited()
    assert session.execute.await_count == 2
    statements = [call.args[0] for call in session.execute.await_args_list]
    revoke, admit = [
        str(
            stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        for stmt in statements
    ]
    for sql in (revoke, admit):
        assert f"external_user.organization_id = '{organization_id}'" in sql
        assert "AND external_user.active" in sql
    assert revoke.startswith("UPDATE invitation SET status='REVOKED'")
    assert f"invitation.organization_id = '{organization_id}'" in revoke
    assert "invitation.status = 'PENDING'" in revoke
    assert 'lower(invitation.email) IN (SELECT lower("user".email)' in revoke
    assert '"user".id IN (SELECT external_user.user_id' in revoke
    assert admit.startswith("INSERT INTO organization_membership")
    assert "SELECT external_user.organization_id, external_user.user_id" in admit
    assert "ON CONFLICT (organization_id, user_id) DO NOTHING" in admit
    session.flush.assert_awaited_once()
    session.commit.assert_not_awaited()


def test_admit_user_uses_same_set_oriented_policy() -> None:
    """Singleton admission narrows the same active-link query to one user."""
    organization_id, user_id = UUID(int=1), UUID(int=2)
    session = AsyncMock()
    service = SCIMService(
        session,
        Role(
            type="service", organization_id=organization_id, service_id="tracecat-api"
        ),
    )

    asyncio.run(service.admit_users(user_id))

    assert session.execute.await_count == 2
    revoke, admit = [
        str(
            call.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        for call in session.execute.await_args_list
    ]
    for sql in (revoke, admit):
        assert f"external_user.organization_id = '{organization_id}'" in sql
        assert "AND external_user.active" in sql
        assert f"external_user.user_id = '{user_id}'" in sql
    assert f"invitation.organization_id = '{organization_id}'" in revoke
    assert "invitation.status = 'PENDING'" in revoke
    assert 'lower(invitation.email) IN (SELECT lower("user".email)' in revoke
    assert "SELECT external_user.organization_id, external_user.user_id" in admit
    assert "ON CONFLICT (organization_id, user_id) DO NOTHING" in admit
    session.scalar.assert_not_awaited()
    session.scalars.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
