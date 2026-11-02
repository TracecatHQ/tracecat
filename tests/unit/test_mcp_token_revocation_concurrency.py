"""MCP token revocation under real tenant RLS and concurrent writers.

``tests/conftest.py`` installs the membership-delete trigger from the migration
that defines it, so these exercise the production SQL. Each test commits
through its own connections to the per-run test database.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.auth.users import UserManager
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.db.models import Organization, User
from tracecat.db.tenant_rls import enable_org_table_rls
from tracecat.mcp.oidc.endpoints import _current_granted_orgs, _issue_code_grant
from tracecat.mcp.oidc.refresh_tokens import (
    RefreshTokenError,
    _hash_token,
    issue_refresh_token,
    rotate_refresh_token,
)
from tracecat.mcp.oidc.schemas import (
    AuthCodeData,
    RefreshTokenContext,
    RefreshTokenMetadata,
)
from tracecat.organization.management import delete_organization_with_cleanup
from tracecat.organization.service import OrgService

CLIENT_ID = "revocation-test-client"

pytestmark = pytest.mark.anyio


@dataclass(frozen=True, slots=True)
class TwoOrgUser:
    user_id: uuid.UUID
    primary_org_id: uuid.UUID
    secondary_org_id: uuid.UUID


@pytest.fixture
async def owner_engine(db: None) -> AsyncIterator[AsyncEngine]:
    del db
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _add_user(engine: AsyncEngine, org_ids: list[uuid.UUID]) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, is_active, '
                "is_superuser, is_verified, role) "
                "VALUES (:id, :email, 'x', true, false, false, 'BASIC')"
            ),
            {"id": user_id, "email": f"grant-{user_id.hex[:8]}@example.com"},
        )
        for org_id in org_ids:
            await conn.execute(
                text(
                    "INSERT INTO organization_membership (user_id, organization_id) "
                    "VALUES (:user_id, :organization_id)"
                ),
                {"user_id": user_id, "organization_id": org_id},
            )
    return user_id


async def _seed_two_org_user(engine: AsyncEngine) -> TwoOrgUser:
    primary_org_id, secondary_org_id = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        for org_id in (primary_org_id, secondary_org_id):
            await conn.execute(
                text(
                    "INSERT INTO organization (id, name, slug, is_active) "
                    "VALUES (:id, 'Grant org', :slug, true)"
                ),
                {"id": org_id, "slug": f"grant-{org_id.hex[:8]}"},
            )
    user_id = await _add_user(engine, [primary_org_id, secondary_org_id])
    return TwoOrgUser(user_id, primary_org_id, secondary_org_id)


async def _issue_token(
    engine: AsyncEngine, user_id: uuid.UUID, org_ids: list[uuid.UUID]
) -> str:
    """Issue a refresh token whose primary org is the first granted org."""
    async with AsyncSession(engine) as session:
        return await issue_refresh_token(
            session,
            user_id=user_id,
            organization_id=org_ids[0],
            client_id=CLIENT_ID,
            metadata=RefreshTokenMetadata(
                email="grant@example.com",
                is_platform_superuser=False,
                scope="openid offline_access",
                resource="https://app.example.com/mcp",
                organization_ids=org_ids,
            ),
        )


async def _issue_two_org_token(engine: AsyncEngine, user: TwoOrgUser) -> str:
    return await _issue_token(
        engine, user.user_id, [user.primary_org_id, user.secondary_org_id]
    )


async def _keep_grant(ctx: RefreshTokenContext) -> tuple[uuid.UUID, list[uuid.UUID]]:
    return ctx.organization_id, ctx.metadata.organization_ids or [ctx.organization_id]


async def _token_status(engine: AsyncEngine, token: str) -> str:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT status FROM mcp_refresh_token WHERE token_hash = :h"),
                {"h": _hash_token(token)},
            )
        ).scalar_one()


async def test_revocation_trigger_runs_as_owner_with_pinned_search_path(
    owner_engine: AsyncEngine,
) -> None:
    """Invariant: the installed trigger function is SECURITY DEFINER, pinned, private."""
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT prosecdef, proconfig, "
                    "has_function_privilege('public', oid, 'EXECUTE') "
                    "FROM pg_proc WHERE oid = "
                    "'public.revoke_mcp_tokens_on_membership_delete()'::regprocedure"
                )
            )
        ).one()
    assert tuple(row) == (True, ["search_path=pg_catalog, pg_temp"], False)


async def test_secondary_org_admin_revokes_token_owned_by_primary_org(
    owner_engine: AsyncEngine,
) -> None:
    """Invariant: the deleting org's RLS scope cannot hide the token row.

    A secondary-org admin's session only sees rows in that org, while the
    token belongs to the primary org. The trigger must still revoke it.
    """
    user = await _seed_two_org_user(owner_engine)
    token = await _issue_two_org_token(owner_engine, user)
    runtime_role = f"mcp_runtime_{uuid.uuid4().hex[:8]}"

    async with owner_engine.connect() as conn:
        transaction = await conn.begin()
        try:
            # The tenant policies and the non-owner role exist only inside
            # this transaction, which is rolled back.
            for table in ("organization_membership", "mcp_refresh_token"):
                for statement in enable_org_table_rls(table).split(";"):
                    if statement.strip():
                        await conn.execute(text(statement))
            await conn.execute(text(f'CREATE ROLE "{runtime_role}" NOLOGIN'))
            await conn.execute(
                text(
                    "GRANT SELECT, DELETE ON organization_membership, "
                    f'mcp_refresh_token TO "{runtime_role}"'
                )
            )
            await conn.execute(text(f'SET LOCAL ROLE "{runtime_role}"'))
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
            await conn.execute(text("RESET ROLE"))
            status = (
                await conn.execute(
                    text("SELECT status FROM mcp_refresh_token WHERE token_hash = :h"),
                    {"h": _hash_token(token)},
                )
            ).scalar_one()
            assert status == "revoked"
        finally:
            await transaction.rollback()


async def test_token_without_grant_column_is_revoked_with_its_primary_org(
    owner_engine: AsyncEngine,
) -> None:
    """Tokens written before the grant column existed keep primary-org revocation."""
    user = await _seed_two_org_user(owner_engine)
    token = await _issue_two_org_token(owner_engine, user)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE mcp_refresh_token SET granted_organization_ids = NULL "
                "WHERE token_hash = :h"
            ),
            {"h": _hash_token(token)},
        )
        await conn.execute(
            text(
                "DELETE FROM organization_membership "
                "WHERE user_id = :u AND organization_id = :o"
            ),
            {"u": user.user_id, "o": user.primary_org_id},
        )

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


async def _wait_until_paused_or_blocked(
    engine: AsyncEngine, paused: asyncio.Event, pid: int
) -> None:
    """Wait until a task reaches its pause point or blocks on a lock."""
    async with engine.connect() as conn:
        for _ in range(200):
            if paused.is_set():
                return
            blocked = (
                await conn.execute(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                    {"pid": pid},
                )
            ).scalar_one()
            if blocked:
                return
            await asyncio.sleep(0.05)
    raise AssertionError(f"backend {pid} neither paused nor blocked")


@dataclass(frozen=True, slots=True)
class Pause:
    reached: asyncio.Event
    resume: asyncio.Event


def _pause_before_org_delete(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> Pause:
    """Hold org cleanup after its explicit deletes, before the org row goes."""
    pause = Pause(asyncio.Event(), asyncio.Event())
    delete_row = session.delete

    async def pause_before_org_delete(instance: object) -> None:
        if isinstance(instance, Organization):
            pause.reached.set()
            await pause.resume.wait()
        await delete_row(instance)

    monkeypatch.setattr(session, "delete", pause_before_org_delete)
    return pause


async def _backend_pid(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()


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


async def test_org_deletion_racing_rotation_does_not_deadlock(
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: org cleanup takes the user lock before it locks token rows.

    Cleanup deletes the org's refresh tokens, then the org delete cascades into
    the membership trigger. A rotation that arrives in between must wait for
    cleanup instead of holding the user lock while it waits on a deleted row.
    """
    user = await _seed_two_org_user(owner_engine)
    token = await _issue_two_org_token(owner_engine, user)

    async with (
        AsyncSession(owner_engine) as cleanup_session,
        AsyncSession(owner_engine) as refresh_session,
    ):
        organization = await cleanup_session.get(Organization, user.primary_org_id)
        assert organization is not None
        pause = _pause_before_org_delete(monkeypatch, cleanup_session)
        cleanup = asyncio.create_task(
            delete_organization_with_cleanup(
                cleanup_session, organization=organization, operator_user_id=None
            )
        )
        await asyncio.wait_for(pause.reached.wait(), timeout=10)

        refresh_pid = await _backend_pid(refresh_session)
        rotation = asyncio.create_task(
            rotate_refresh_token(
                refresh_session,
                token=token,
                client_id=CLIENT_ID,
                resolve_grant=_keep_grant,
            )
        )
        await _wait_until_blocked(owner_engine, refresh_pid)
        pause.resume.set()
        await asyncio.wait_for(cleanup, timeout=10)
        await cleanup_session.commit()

        with pytest.raises(RefreshTokenError):
            await asyncio.wait_for(rotation, timeout=10)


async def test_concurrent_org_deletions_sharing_members_do_not_deadlock(
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: org cleanup locks every member it will remove up front.

    Orgs A and B share members U and V. U's token is primary to A and V's to
    B, so each cleanup deletes a different token. Both org deletes then
    cascade into both members' trigger locks, so a cleanup that locked only
    its token owners would wait on the other cleanup.
    """
    user = await _seed_two_org_user(owner_engine)
    org_a, org_b = user.primary_org_id, user.secondary_org_id
    other_user_id = await _add_user(owner_engine, [org_a, org_b])
    await _issue_token(owner_engine, user.user_id, [org_a, org_b])
    await _issue_token(owner_engine, other_user_id, [org_b, org_a])

    async with (
        AsyncSession(owner_engine) as session_a,
        AsyncSession(owner_engine) as session_b,
    ):
        organization_a = await session_a.get(Organization, org_a)
        organization_b = await session_b.get(Organization, org_b)
        assert organization_a is not None and organization_b is not None
        pause_a = _pause_before_org_delete(monkeypatch, session_a)
        pause_b = _pause_before_org_delete(monkeypatch, session_b)
        pid_b = await _backend_pid(session_b)

        cleanup_a = asyncio.create_task(
            delete_organization_with_cleanup(
                session_a, organization=organization_a, operator_user_id=None
            )
        )
        await asyncio.wait_for(pause_a.reached.wait(), timeout=10)
        cleanup_b = asyncio.create_task(
            delete_organization_with_cleanup(
                session_b, organization=organization_b, operator_user_id=None
            )
        )
        await _wait_until_paused_or_blocked(owner_engine, pause_b.reached, pid_b)

        pause_a.resume.set()
        pause_b.resume.set()
        await asyncio.wait_for(cleanup_a, timeout=10)
        await session_a.commit()
        await asyncio.wait_for(cleanup_b, timeout=10)
        await session_b.commit()


async def test_user_deletion_racing_member_removal_does_not_deadlock(
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: member removal takes the user lock before the membership row.

    Hard user deletion takes the user lock, then cascades into the user's
    memberships. A removal that deleted a membership first and only then
    reached the lock in the trigger would wait on the deletion forever.
    """
    user = await _seed_two_org_user(owner_engine)
    admin_id = await _add_user(owner_engine, [user.primary_org_id])
    admin_role = Role(
        type="user",
        user_id=admin_id,
        organization_id=user.primary_org_id,
        service_id="tracecat-api",
        is_platform_superuser=False,
        scopes=ORG_ADMIN_SCOPES,
    )

    async with (
        AsyncSession(owner_engine) as delete_session,
        AsyncSession(owner_engine) as removal_session,
    ):
        user_db = SQLAlchemyUserDatabase(delete_session, User)
        manager = UserManager(user_db)
        doomed = await delete_session.get(User, user.user_id)
        assert doomed is not None
        reached_delete = asyncio.Event()
        resume_delete = asyncio.Event()
        delete_user_row = user_db.delete

        async def pause_before_user_delete(instance: User) -> None:
            reached_delete.set()
            await resume_delete.wait()
            await delete_user_row(instance)

        monkeypatch.setattr(user_db, "delete", pause_before_user_delete)
        deletion = asyncio.create_task(manager.delete(doomed))
        await asyncio.wait_for(reached_delete.wait(), timeout=10)

        removal_pid = await _backend_pid(removal_session)
        # The scope decorator types delete_member as a plain Awaitable.
        removal = asyncio.ensure_future(
            OrgService(removal_session, role=admin_role).delete_member(user.user_id)
        )
        await _wait_until_blocked(owner_engine, removal_pid)
        resume_delete.set()
        await asyncio.wait_for(deletion, timeout=10)
        await asyncio.wait_for(removal, timeout=10)


async def _wait_until_done_or_lock_waiter[T](
    engine: AsyncEngine, task: asyncio.Future[T]
) -> None:
    """Wait until a task finishes or some backend in this database waits on a lock."""
    async with engine.connect() as conn:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if task.done():
                return
            waiting = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE NOT granted AND "
                        "database = (SELECT oid FROM pg_database "
                        "WHERE datname = current_database())"
                    )
                )
            ).scalar_one()
            if waiting:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("task neither finished nor waited on a lock")


async def test_code_exchange_racing_membership_removal_drops_removed_org(
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant: a token issued around a removal never keeps the removed org.

    The removal's trigger holds the user lock until it commits. The exchange
    must read memberships after that, not insert a token the trigger has
    already passed over.
    """
    user = await _seed_two_org_user(owner_engine)

    @contextlib.asynccontextmanager
    async def test_db_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(owner_engine) as session:
            yield session

    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.get_async_session_bypass_rls_context_manager",
        test_db_session,
    )
    code_data = AuthCodeData(
        code="race-code",
        user_id=user.user_id,
        email="grant@example.com",
        organization_id=user.primary_org_id,
        organization_ids=[user.primary_org_id, user.secondary_org_id],
        is_platform_superuser=False,
        client_id=CLIENT_ID,
        redirect_uri="https://client.example.com/callback",
        code_challenge="unused",
        scope="openid offline_access",
        resource="https://app.example.com/mcp",
        created_at=time.time(),
        bound_ip="127.0.0.1",
    )

    async with owner_engine.connect() as remover:
        await remover.execute(
            text(
                "DELETE FROM organization_membership "
                "WHERE user_id = :u AND organization_id = :o"
            ),
            {"u": user.user_id, "o": user.secondary_org_id},
        )
        exchange = asyncio.ensure_future(
            _issue_code_grant(code_data, client_id=CLIENT_ID, scope=code_data.scope)
        )
        await _wait_until_done_or_lock_waiter(owner_engine, exchange)
        await remover.commit()

    primary_org_id, granted_org_ids, token = await asyncio.wait_for(exchange, 10)
    assert primary_org_id == user.primary_org_id
    assert granted_org_ids == [user.primary_org_id]
    assert token is not None
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, granted_organization_ids FROM mcp_refresh_token "
                    "WHERE token_hash = :h"
                ),
                {"h": _hash_token(token)},
            )
        ).one()
    assert tuple(row) == ("active", [user.primary_org_id])
