"""Tests for the membership backfill guard in the RBAC-derived membership migration.

The migration exposes ``backfill_assignments`` and ``assert_no_membership_dropped``
as plain functions over a SQLAlchemy ``Connection`` so the guard can be exercised
without running alembic end-to-end. The legacy ``membership`` and
``organization_membership`` tables no longer exist in the model metadata, so each
test recreates them for the duration of its own transaction.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.seeding import seed_system_roles_for_org
from tracecat.db.models import Organization, User, Workspace

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "2bf069003a77_membership_derived_from_rbac_assignments.py"
)

pytestmark = pytest.mark.usefixtures("db")

CREATE_LEGACY_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS membership (
        user_id uuid NOT NULL,
        workspace_id uuid NOT NULL,
        PRIMARY KEY (user_id, workspace_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS organization_membership (
        user_id uuid NOT NULL,
        organization_id uuid NOT NULL,
        PRIMARY KEY (user_id, organization_id)
    )
    """,
)

DROP_LEGACY_TABLES = (
    "DROP TABLE IF EXISTS membership",
    "DROP TABLE IF EXISTS organization_membership",
)


@pytest.fixture
def migration() -> Any:
    """Import the migration module by file path; alembic versions aren't a package."""
    module_name = "_membership_derived_migration_under_test"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def legacy_tables(session: AsyncSession):
    for statement in CREATE_LEGACY_TABLES:
        await session.execute(sa.text(statement))
    await session.commit()
    yield
    for statement in DROP_LEGACY_TABLES:
        await session.execute(sa.text(statement))
    await session.commit()


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Membership Guard Org",
        slug=f"guard-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.commit()
    return organization


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name="guard-workspace", organization_id=org.id)
    session.add(ws)
    await session.commit()
    return ws


@pytest.fixture
async def user(session: AsyncSession) -> User:
    account = User(
        id=uuid.uuid4(),
        email=f"guard-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(account)
    await session.commit()
    return account


async def _run_sync(session: AsyncSession, fn, *args):
    """Run a sync Connection-taking migration helper on the async session's bind."""
    connection = await session.connection()
    return await connection.run_sync(lambda conn: fn(conn, *args))


@pytest.mark.anyio
async def test_guard_passes_when_backfill_covers_every_membership(
    session: AsyncSession,
    migration: Any,
    legacy_tables: None,
    org: Organization,
    workspace: Workspace,
    user: User,
) -> None:
    """With system roles seeded, the backfill covers both memberships."""
    await seed_system_roles_for_org(session, org.id)
    await session.execute(
        sa.text(
            "INSERT INTO membership (user_id, workspace_id) VALUES (:u, :w)"
        ).bindparams(u=user.id, w=workspace.id)
    )
    await session.execute(
        sa.text(
            "INSERT INTO organization_membership (user_id, organization_id) "
            "VALUES (:u, :o)"
        ).bindparams(u=user.id, o=org.id)
    )
    await session.commit()

    workspace_count, org_count = await _run_sync(
        session, migration.backfill_assignments
    )
    assert workspace_count >= 1
    assert org_count >= 0

    await _run_sync(session, migration.assert_no_membership_dropped)


@pytest.mark.anyio
async def test_guard_raises_when_org_lacks_system_roles(
    session: AsyncSession,
    migration: Any,
    legacy_tables: None,
    org: Organization,
    workspace: Workspace,
    user: User,
) -> None:
    """Without seeded system roles the backfill skips the rows and the guard fails."""
    await session.execute(
        sa.text(
            "INSERT INTO membership (user_id, workspace_id) VALUES (:u, :w)"
        ).bindparams(u=user.id, w=workspace.id)
    )
    await session.execute(
        sa.text(
            "INSERT INTO organization_membership (user_id, organization_id) "
            "VALUES (:u, :o)"
        ).bindparams(u=user.id, o=org.id)
    )
    await session.commit()

    await _run_sync(session, migration.backfill_assignments)

    with pytest.raises(RuntimeError) as excinfo:
        await _run_sync(session, migration.assert_no_membership_dropped)

    message = str(excinfo.value)
    assert "1 workspace membership(s)" in message
    assert "1 organization membership(s)" in message
    assert str(org.id) in message


@pytest.mark.anyio
async def test_guard_sql_is_valid_against_the_legacy_schema(
    session: AsyncSession, migration: Any, legacy_tables: None
) -> None:
    """The guard statements execute and return zero on an empty legacy schema."""
    for statement in (
        migration.UNCOVERED_WORKSPACE_MEMBERSHIPS,
        migration.UNCOVERED_ORG_MEMBERSHIPS,
    ):
        row = (await session.execute(sa.text(statement))).one()
        assert row.uncovered == 0
        assert list(row.organization_ids) == []
