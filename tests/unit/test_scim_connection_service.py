"""Unit tests for the SCIM connection credential lifecycle."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.connections import ScimConnectionService

from tracecat.auth.api_keys import (
    SCIM_API_KEY_PREFIX,
    parse_managed_api_key,
    verify_api_key,
)
from tracecat.auth.types import Role
from tracecat.db.models import Organization, ScimConnection, User
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="SCIM Org", slug=f"scim-org-{org_id.hex[:8]}")
    session.add(org)
    await session.flush()
    return org


@pytest.fixture
async def admin_user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
def admin_role(org: Organization, admin_user: User) -> Role:
    return Role(
        type="user",
        user_id=admin_user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({"org:scim:manage"}),
    )


@pytest.mark.anyio
async def test_issue_token_returns_verifiable_token(
    session: AsyncSession, org: Organization, admin_role: Role
) -> None:
    """The raw token verifies against the stored hash and salt."""
    service = ScimConnectionService(session, role=admin_role)
    issued = await service.issue_token()

    assert issued.token.startswith(SCIM_API_KEY_PREFIX)
    parsed = parse_managed_api_key(issued.token, prefixes=(SCIM_API_KEY_PREFIX,))
    assert parsed is not None
    assert parsed.key_id == issued.connection.key_id
    assert verify_api_key(
        issued.token, issued.connection.salt, issued.connection.hashed
    )
    assert issued.connection.organization_id == org.id
    # The preview is safe to show; it is not the token.
    assert issued.token not in issued.connection.preview


@pytest.mark.anyio
async def test_rotation_invalidates_the_previous_token(
    session: AsyncSession, org: Organization, admin_role: Role
) -> None:
    """Rotating reuses the row, so the old token stops verifying."""
    service = ScimConnectionService(session, role=admin_role)
    first = await service.issue_token()
    first_token = first.token
    first_key_id = first.connection.key_id

    second = await service.issue_token()

    assert second.token != first_token
    assert second.connection.key_id != first_key_id
    assert not verify_api_key(
        first_token, second.connection.salt, second.connection.hashed
    )
    assert verify_api_key(
        second.token, second.connection.salt, second.connection.hashed
    )

    # Rotation must not create a second row for the organization.
    rows = (
        (
            await session.execute(
                select(ScimConnection).where(ScimConnection.organization_id == org.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.anyio
async def test_revoke_marks_the_connection_revoked(
    session: AsyncSession, org: Organization, admin_role: Role
) -> None:
    service = ScimConnectionService(session, role=admin_role)
    await service.issue_token()

    await service.revoke()

    connection = await service.get_connection()
    assert connection.revoked_at is not None


@pytest.mark.anyio
async def test_get_connection_without_one_raises(
    session: AsyncSession, org: Organization, admin_role: Role
) -> None:
    service = ScimConnectionService(session, role=admin_role)
    with pytest.raises(TracecatNotFoundError):
        await service.get_connection()


@pytest.mark.anyio
async def test_one_connection_per_organization(
    session: AsyncSession, org: Organization, admin_role: Role
) -> None:
    """The unique constraint refuses a second connection for the same org."""
    service = ScimConnectionService(session, role=admin_role)
    await service.issue_token()

    session.add(
        ScimConnection(
            organization_id=org.id,
            key_id=uuid.uuid4().hex[:12],
            hashed="x" * 16,
            salt="y" * 16,
            preview=f"{SCIM_API_KEY_PREFIX}...zzzz",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.anyio
async def test_issue_token_requires_scope(
    session: AsyncSession, org: Organization
) -> None:
    """Unrelated membership permissions do not allow issuing a token."""
    unprivileged = Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({"org:member:read"}),
    )
    service = ScimConnectionService(session, role=unprivileged)
    with pytest.raises(ScopeDeniedError):
        await service.issue_token()


@pytest.mark.anyio
async def test_connection_management_requires_explicit_scim_permission(
    session: AsyncSession, org: Organization
) -> None:
    """Even full RBAC and member authority does not grant SCIM administration."""
    partial = Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({"org:rbac:*", "org:member:*"}),
    )
    service = ScimConnectionService(session, role=partial)
    with pytest.raises(ScopeDeniedError):
        await service.issue_token()

    with pytest.raises(ScopeDeniedError):
        await service.get_connection()
    with pytest.raises(ScopeDeniedError):
        await service.revoke()
