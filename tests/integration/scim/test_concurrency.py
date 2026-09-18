"""Controlled PostgreSQL interleavings for SCIM and RBAC writers.

These use committed synthetic fixtures and independent READ COMMITTED sessions;
SQLite and the shared unit-test savepoint cannot prove row-lock behavior.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from tracecat_ee.rbac.service import RBACService
from tracecat_ee.scim.protocol import patch_group
from tracecat_ee.scim.provisioning import ScimProvisioningService
from tracecat_ee.scim.schemas import ScimPatchOp
from tracecat_ee.scim.service import SCIMService

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.authz.membership import lock_role_changes
from tracecat.db.models import (
    ExternalGroup,
    ExternalGroupMapping,
    ExternalGroupMember,
    ExternalUser,
    Group,
    GroupMember,
    Organization,
    OrganizationMembership,
    ScimConnection,
    User,
)
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@dataclass(frozen=True)
class Cohort:
    engine: AsyncEngine
    org_id: uuid.UUID
    group_id: uuid.UUID
    source_id: uuid.UUID
    users: list[uuid.UUID]
    external: list[uuid.UUID]

    @property
    def role(self) -> Role:
        return Role(
            type="service",
            service_id="tracecat-api",
            organization_id=self.org_id,
            scopes=frozenset(
                {
                    "org:rbac:read",
                    "org:rbac:create",
                    "org:rbac:update",
                    "org:member:remove",
                }
            ),
        )


@pytest.fixture
async def cohort() -> AsyncIterator[Cohort]:
    engine = create_async_engine(
        TEST_DB_CONFIG.test_url, isolation_level="READ COMMITTED", poolclass=NullPool
    )
    org_id, group_id, source_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    users = [uuid.uuid4() for _ in range(4)]
    external = [uuid.uuid4() for _ in range(3)]
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            Organization(id=org_id, name="Concurrency test", slug=f"race-{org_id.hex}")
        )
        session.add_all(
            [
                User(
                    id=uid,
                    email=f"race-{uid.hex}@example.com",
                    hashed_password="unused",
                )
                for uid in users
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(organization_id=org_id, user_id=uid)
                for uid in users[:3]
            ]
        )
        session.add_all(
            [
                ScimConnection(
                    organization_id=org_id,
                    key_id=org_id.hex[:16],
                    hashed="unused",
                    salt="unused",
                    preview="scim_test",
                    status=ScimConnectionStatus.ACTIVE,
                ),
                Group(id=group_id, organization_id=org_id, name="Target"),
                ExternalGroup(
                    id=source_id,
                    organization_id=org_id,
                    external_id="source",
                    display_name="Source",
                ),
            ]
        )
        session.add_all(
            [
                ExternalUser(
                    id=eid,
                    organization_id=org_id,
                    user_id=uid,
                    external_id=str(uid),
                    active=True,
                )
                for uid, eid in zip(users, external, strict=False)
            ]
        )
        await session.flush()
        session.add(
            ExternalGroupMember(
                organization_id=org_id,
                external_group_id=source_id,
                external_user_id=external[0],
            )
        )
        await session.commit()
    try:
        yield Cohort(engine, org_id, group_id, source_id, users, external)
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete(Organization).where(Organization.id == org_id))
            for uid in users:
                user = await session.get(User, uid)
                if user is not None:
                    await session.delete(user)
            await session.commit()
        await engine.dispose()


async def interleave(
    cohort: Cohort,
    monkeypatch: pytest.MonkeyPatch,
    first: Callable[[AsyncSession], Awaitable[object]],
    second: Callable[[AsyncSession], Awaitable[object]],
) -> list[object]:
    held, attempted, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with (
        AsyncSession(cohort.engine, expire_on_commit=False) as a,
        AsyncSession(cohort.engine, expire_on_commit=False) as b,
    ):

        async def gate(session: AsyncSession, org_id: uuid.UUID) -> None:
            if session is b:
                attempted.set()
            await lock_role_changes(session, org_id)
            if session is a and not held.is_set():
                held.set()
                await release.wait()

        for module in (
            "tracecat_ee.scim.service",
            "tracecat_ee.scim.protocol",
            "tracecat_ee.scim.provisioning",
            "tracecat_ee.rbac.service",
        ):
            monkeypatch.setattr(f"{module}.lock_role_changes", gate)
        task_a = asyncio.ensure_future(first(a))
        task_b = None
        try:
            await asyncio.wait_for(held.wait(), 5)
            task_b = asyncio.ensure_future(second(b))
            await asyncio.wait_for(attempted.wait(), 5)
            release.set()
            return list(await asyncio.wait_for(asyncio.gather(task_a, task_b), 10))
        finally:
            release.set()
            for task in (task_a, task_b):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (task_a, task_b) if task is not None),
                return_exceptions=True,
            )


@pytest.mark.parametrize("replace_first", [False, True])
async def test_group_updates_serialize(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, replace_first: bool
) -> None:
    async def change(session: AsyncSession, op: str, ids: list[uuid.UUID]) -> object:
        return await patch_group(
            role=cohort.role,
            session=session,
            group_id=cohort.source_id,
            params=ScimPatchOp.model_validate(
                {
                    "Operations": [
                        {
                            "op": op,
                            "path": "members",
                            "value": [{"value": str(eid)} for eid in ids],
                        }
                    ]
                }
            ),
        )

    async def first(session: AsyncSession) -> object:
        return await change(
            session, "replace" if replace_first else "add", [cohort.external[1]]
        )

    async def second(session: AsyncSession) -> object:
        return await change(
            session, "remove" if replace_first else "add", [cohort.external[2]]
        )

    await interleave(cohort, monkeypatch, first, second)
    async with AsyncSession(cohort.engine) as session:
        actual = set(
            (
                await session.execute(
                    select(ExternalGroupMember.external_user_id).where(
                        ExternalGroupMember.external_group_id == cohort.source_id
                    )
                )
            ).scalars()
        )
    assert actual == ({cohort.external[1]} if replace_first else set(cohort.external))


@pytest.mark.parametrize("mapping_first", [False, True])
async def test_mapping_and_manual_add_share_ownership_boundary(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, mapping_first: bool
) -> None:
    async def mapping(session: AsyncSession) -> object:
        await SCIMService(session, cohort.role).create_mapping(
            external_group_id=cohort.source_id, group_id=cohort.group_id
        )
        await session.commit()

    async def manual(session: AsyncSession) -> object:
        try:
            await RBACService(session, cohort.role).add_group_member(
                cohort.group_id, cohort.users[2]
            )
        except TracecatConflictError:
            await session.rollback()
            return "managed"

    results = await interleave(
        cohort,
        monkeypatch,
        mapping if mapping_first else manual,
        manual if mapping_first else mapping,
    )
    if mapping_first:
        assert results[1] == "managed"
    async with AsyncSession(cohort.engine) as session:
        assert (
            await session.execute(
                select(GroupMember).where(GroupMember.group_id == cohort.group_id)
            )
        ).scalars().all() == []


@pytest.mark.parametrize("activation_first", [False, True])
async def test_activation_and_provisioning_admit_complete_directory(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, activation_first: bool
) -> None:
    async with AsyncSession(cohort.engine) as session:
        connection = (
            await session.execute(
                select(ScimConnection).where(
                    ScimConnection.organization_id == cohort.org_id
                )
            )
        ).scalar_one()
        connection.status = ScimConnectionStatus.PENDING
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == cohort.org_id
            )
        )
        await session.commit()

    async def activate(session: AsyncSession) -> object:
        await SCIMService(session, cohort.role).activate([])

    async def provision(session: AsyncSession) -> object:
        await ScimProvisioningService(session, cohort.role).provision_user(
            email=f"race-{cohort.users[3].hex}@example.com", external_id="new-user"
        )
        await session.commit()

    await interleave(
        cohort,
        monkeypatch,
        activate if activation_first else provision,
        provision if activation_first else activate,
    )
    async with AsyncSession(cohort.engine) as session:
        actual = set(
            (
                await session.execute(
                    select(OrganizationMembership.user_id).where(
                        OrganizationMembership.organization_id == cohort.org_id
                    )
                )
            ).scalars()
        )
    assert actual == set(cohort.users)


@pytest.mark.parametrize("delete_first", [False, True])
async def test_source_delete_and_mapping_have_controlled_outcomes(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, delete_first: bool
) -> None:
    async def remove(session: AsyncSession) -> object:
        await SCIMService(session, cohort.role).delete_external_group(cohort.source_id)
        await session.commit()

    async def mapping(session: AsyncSession) -> object:
        try:
            await SCIMService(session, cohort.role).create_mapping(
                external_group_id=cohort.source_id, group_id=cohort.group_id
            )
            await session.commit()
        except TracecatNotFoundError:
            await session.rollback()
            return "missing"

    results = await interleave(
        cohort,
        monkeypatch,
        remove if delete_first else mapping,
        mapping if delete_first else remove,
    )
    if delete_first:
        assert results[1] == "missing"
    async with AsyncSession(cohort.engine) as session:
        assert (
            await session.execute(
                select(ExternalGroupMapping).where(
                    ExternalGroupMapping.organization_id == cohort.org_id
                )
            )
        ).scalars().all() == []
