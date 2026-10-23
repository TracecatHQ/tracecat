"""Removing a user from any granted org revokes their MCP refresh tokens.

These run against the migrated schema, so the trigger and tenant RLS policies
are the production ones rather than the copies installed by ``tests/conftest``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.mcp.oidc.endpoints import _current_granted_orgs
from tracecat.mcp.oidc.refresh_tokens import (
    _hash_token,
    issue_refresh_token,
    rotate_refresh_token,
)
from tracecat.mcp.oidc.schemas import RefreshTokenContext, RefreshTokenMetadata

MIGRATION_REVISION = "c4e8a2f6d9b1"
CLIENT_ID = "migration-test-client"

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class MigratedDatabase:
    sync_url: str
    async_url: str
    runtime_role: str


@dataclass(frozen=True, slots=True)
class TwoOrgUser:
    user_id: uuid.UUID
    primary_org_id: uuid.UUID
    secondary_org_id: uuid.UUID


def _run_alembic(db_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = db_url
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Alembic command failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


@pytest.fixture(scope="module")
def migrated_db() -> Iterator[MigratedDatabase]:
    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync,
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    suffix = uuid.uuid4().hex[:8]
    db_name = f"test_mcp_grant_revocation_{suffix}"
    runtime_role = f"mcp_runtime_{suffix}"
    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{db_name}'
          AND pid <> pg_backend_pid();
        """
    )
    try:
        with default_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            conn.execute(text(f'CREATE ROLE "{runtime_role}" NOLOGIN'))

        sync_url = TEST_DB_CONFIG.test_url_sync.replace(
            TEST_DB_CONFIG.test_db_name, db_name
        )
        _run_alembic(sync_url, "upgrade", MIGRATION_REVISION)
        # A non-owner role, so PostgreSQL applies the tenant policies.
        engine = create_engine(sync_url, poolclass=NullPool)
        with engine.begin() as conn:
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{runtime_role}"'))
            conn.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA public TO "{runtime_role}"'
                )
            )
        engine.dispose()
        async_url = (
            make_url(sync_url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False)
        )
        yield MigratedDatabase(sync_url, async_url, runtime_role)
    finally:
        with default_engine.connect() as conn:
            conn.execute(termination_query)
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{runtime_role}"'))
        default_engine.dispose()


@pytest.fixture
async def owner_engine(migrated_db: MigratedDatabase) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_db.async_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _seed_two_org_user(engine: AsyncEngine) -> TwoOrgUser:
    user_id, primary_org_id, secondary_org_id = (uuid.uuid4() for _ in range(3))
    async with engine.begin() as conn:
        for org_id in (primary_org_id, secondary_org_id):
            await conn.execute(
                text(
                    "INSERT INTO organization (id, name, slug, is_active) "
                    "VALUES (:id, 'Grant org', :slug, true)"
                ),
                {"id": org_id, "slug": f"grant-{org_id.hex[:8]}"},
            )
        await conn.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, is_active, '
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'x', true, false, false, 'BASIC')"
            ),
            {"id": user_id, "email": f"grant-{user_id.hex[:8]}@example.com"},
        )
        for org_id in (primary_org_id, secondary_org_id):
            await conn.execute(
                text(
                    "INSERT INTO organization_membership (user_id, organization_id) "
                    "VALUES (:user_id, :organization_id)"
                ),
                {"user_id": user_id, "organization_id": org_id},
            )
    return TwoOrgUser(user_id, primary_org_id, secondary_org_id)


async def _issue_two_org_token(engine: AsyncEngine, user: TwoOrgUser) -> str:
    async with AsyncSession(engine) as session:
        return await issue_refresh_token(
            session,
            user_id=user.user_id,
            organization_id=user.primary_org_id,
            client_id=CLIENT_ID,
            metadata=RefreshTokenMetadata(
                email="grant@example.com",
                is_platform_superuser=False,
                scope="openid offline_access",
                resource="https://app.example.com/mcp",
                organization_ids=[user.primary_org_id, user.secondary_org_id],
            ),
        )


async def _token_status(engine: AsyncEngine, token: str) -> str:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT status FROM mcp_refresh_token WHERE token_hash = :h"),
                {"h": _hash_token(token)},
            )
        ).scalar_one()


async def test_secondary_org_admin_revokes_token_owned_by_primary_org(
    migrated_db: MigratedDatabase,
    owner_engine: AsyncEngine,
) -> None:
    """Invariant: the deleting org's RLS scope cannot hide the token row.

    A secondary-org admin's session only sees rows in that org, while the
    token belongs to the primary org. The trigger must still revoke it.
    """
    user = await _seed_two_org_user(owner_engine)
    token = await _issue_two_org_token(owner_engine, user)

    runtime_engine = create_async_engine(
        migrated_db.async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"role": migrated_db.runtime_role}},
    )
    try:
        async with runtime_engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(user.secondary_org_id)},
            )
            visible = (
                await conn.execute(
                    text("SELECT count(*) FROM mcp_refresh_token WHERE user_id = :u"),
                    {"u": user.user_id},
                )
            ).scalar_one()
            assert visible == 0, "RLS should hide the primary org's token row"
            deleted = await conn.execute(
                text(
                    "DELETE FROM organization_membership "
                    "WHERE user_id = :u AND organization_id = :o"
                ),
                {"u": user.user_id, "o": user.secondary_org_id},
            )
            assert deleted.rowcount == 1
    finally:
        await runtime_engine.dispose()

    assert await _token_status(owner_engine, token) == "revoked"


async def _wait_until_blocked(engine: AsyncEngine, pid: int) -> None:
    async with engine.connect() as conn:
        for _ in range(200):
            blocked = (
                await conn.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                    {"pid": pid},
                )
            ).scalar_one()
            if blocked:
                return
            await asyncio.sleep(0.05)
    raise AssertionError(f"backend {pid} never blocked")


async def test_membership_removal_racing_rotation_revokes_replacement(
    owner_engine: AsyncEngine,
) -> None:
    """Invariant: a removal that commits after a rotation revokes its replacement.

    The rotation reads the memberships before the removal commits, so it keeps
    the secondary org in the replacement token. The removal has to see that
    replacement once the rotation commits.
    """
    user = await _seed_two_org_user(owner_engine)
    token = await _issue_two_org_token(owner_engine, user)

    async with AsyncSession(owner_engine) as refresh_session:

        async def resolve_grant(
            ctx: RefreshTokenContext,
        ) -> tuple[uuid.UUID, list[uuid.UUID]]:
            return await _current_granted_orgs(
                refresh_session,
                user_id=ctx.user_id,
                primary_org_id=ctx.organization_id,
                approved_org_ids=ctx.metadata.organization_ids,
            )

        ctx, replacement = await rotate_refresh_token(
            refresh_session,
            token=token,
            client_id=CLIENT_ID,
            resolve_grant=resolve_grant,
        )
        assert user.secondary_org_id in (ctx.metadata.organization_ids or [])
        await refresh_session.flush()

        async with owner_engine.connect() as remover:
            pid = (await remover.execute(text("SELECT pg_backend_pid()"))).scalar_one()

            async def remove_secondary_membership() -> None:
                await remover.execute(
                    text(
                        "DELETE FROM organization_membership "
                        "WHERE user_id = :u AND organization_id = :o"
                    ),
                    {"u": user.user_id, "o": user.secondary_org_id},
                )
                await remover.commit()

            removal = asyncio.create_task(remove_secondary_membership())
            await _wait_until_blocked(owner_engine, pid)
            await refresh_session.commit()
            await asyncio.wait_for(removal, timeout=10)

    assert await _token_status(owner_engine, token) == "used"
    assert await _token_status(owner_engine, replacement) == "revoked"
