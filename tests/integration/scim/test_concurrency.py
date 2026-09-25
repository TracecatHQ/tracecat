"""Controlled PostgreSQL interleavings for SCIM and RBAC writers.

These use committed synthetic fixtures and independent READ COMMITTED sessions;
SQLite and the shared unit-test savepoint cannot prove row-lock behavior.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
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
    GroupRoleAssignment,
    Membership,
    Organization,
    OrganizationMembership,
    ScimConnection,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
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
                    "org:scim:manage",
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


async def test_duplicate_first_provisioning_links_the_winning_account(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = f"duplicate-{uuid.uuid4().hex}@tracecat.com"
    ready, both_ready = 0, asyncio.Event()
    original_create = SQLAlchemyUserDatabase.create

    async def create(
        db: SQLAlchemyUserDatabase[User, uuid.UUID], values: dict[str, Any]
    ) -> User:
        nonlocal ready
        ready += 1
        if ready == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), 10)
        return await original_create(db, values)

    @asynccontextmanager
    async def auth_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(cohort.engine) as session:
            yield session

    monkeypatch.setattr(SQLAlchemyUserDatabase, "create", create)
    monkeypatch.setattr(
        "tracecat.auth.users.get_async_session_auth_context_manager", auth_session
    )

    async def provision() -> uuid.UUID:
        async with AsyncSession(cohort.engine, expire_on_commit=False) as session:
            result = await ScimProvisioningService(session, cohort.role).provision_user(
                email=email, external_id="same-idp-user"
            )
            await session.commit()
            return result.user.id

    try:
        results = await asyncio.wait_for(asyncio.gather(provision(), provision()), 20)
        assert ready == 2
        assert results[0] == results[1]
        async with AsyncSession(cohort.engine) as session:
            assert (
                (
                    await session.execute(
                        select(ExternalUser).where(ExternalUser.user_id == results[0])
                    )
                )
                .scalars()
                .one()
                .active
            )
            assert (
                await session.get(OrganizationMembership, (results[0], cohort.org_id))
                is not None
            )
    finally:
        async with AsyncSession(cohort.engine) as session:
            await session.execute(delete(User).where(User.__table__.c.email == email))
            await session.commit()


@pytest.fixture
async def mapped_directory(
    cohort: Cohort,
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """Three admitted users, one unadmitted shadow, and independent access paths."""
    workspace_id, manual_id, role_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with AsyncSession(cohort.engine, expire_on_commit=False) as session:
        session.add_all(
            [
                Workspace(
                    id=workspace_id, organization_id=cohort.org_id, name="Lifecycle"
                ),
                Group(id=manual_id, organization_id=cohort.org_id, name="Independent"),
                DBRole(id=role_id, organization_id=cohort.org_id, name="Access"),
                ExternalUser(
                    id=uuid.uuid4(),
                    organization_id=cohort.org_id,
                    user_id=cohort.users[3],
                    external_id="unadmitted",
                    active=True,
                ),
            ]
        )
        await session.flush()
        external_ids = list(
            (
                await session.scalars(
                    select(ExternalUser.id).where(
                        ExternalUser.organization_id == cohort.org_id
                    )
                )
            ).all()
        )
        service = SCIMService(session, cohort.role)
        await service.replace_external_group_members(cohort.source_id, external_ids)
        mapping = await service.create_mapping(
            external_group_id=cohort.source_id, group_id=cohort.group_id
        )
        for group_id in (cohort.group_id, manual_id):
            session.add(
                GroupRoleAssignment(
                    id=uuid.uuid4(),
                    organization_id=cohort.org_id,
                    group_id=group_id,
                    role_id=role_id,
                    workspace_id=workspace_id,
                )
            )
        for user_id in cohort.users[:2]:
            session.add(
                GroupMember(
                    organization_id=cohort.org_id, group_id=manual_id, user_id=user_id
                )
            )
            session.add(
                UserRoleAssignment(
                    id=uuid.uuid4(),
                    organization_id=cohort.org_id,
                    user_id=user_id,
                    role_id=role_id,
                    workspace_id=workspace_id,
                )
            )
        await session.commit()
        mapping_id = mapping.id
    try:
        yield mapping_id, workspace_id, manual_id
    finally:
        async with AsyncSession(cohort.engine) as session:
            await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await session.commit()


async def change_directory_group(
    cohort: Cohort, mapping_id: uuid.UUID, operation: str, session: AsyncSession
) -> None:
    role = cohort.role.model_copy(
        update={"scopes": (cohort.role.scopes or frozenset()) | {"org:rbac:delete"}}
    )
    service = SCIMService(session, role)
    if operation == "unmap":
        await service.delete_mapping(mapping_id)
    elif operation == "delete":
        await service.delete_external_group(cohort.source_id)
    else:
        # Remove only user 0; leave both peers and the unadmitted shadow in place.
        external_ids = list(
            (
                await session.scalars(
                    select(ExternalUser.id).where(
                        ExternalUser.organization_id == cohort.org_id,
                        ExternalUser.user_id != cohort.users[0],
                    )
                )
            ).all()
        )
        await service.replace_external_group_members(cohort.source_id, external_ids)
    await session.commit()


async def assert_directory_outcome(
    cohort: Cohort,
    directory: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    operation: str,
    deprovisioned: bool,
) -> None:
    _, workspace_id, manual_id = directory
    admitted = set(cohort.users[1:3] if deprovisioned else cohort.users[:3])
    independent = set(cohort.users[1:2] if deprovisioned else cohort.users[:2])
    async with AsyncSession(cohort.engine) as session:
        assert (
            set(
                (
                    await session.scalars(
                        select(OrganizationMembership.user_id).where(
                            OrganizationMembership.organization_id == cohort.org_id
                        )
                    )
                ).all()
            )
            == admitted
        )
        for statement in (
            select(GroupMember.user_id).where(GroupMember.group_id == manual_id),
            select(UserRoleAssignment.user_id).where(
                UserRoleAssignment.organization_id == cohort.org_id
            ),
        ):
            assert set((await session.scalars(statement)).all()) == independent
        retained = set(
            (
                await session.scalars(
                    select(GroupMember.user_id).where(
                        GroupMember.group_id == cohort.group_id
                    )
                )
            ).all()
        )
        assert retained == (admitted if operation == "unmap" else set())
        paths = set(
            (
                await session.scalars(
                    select(Membership.user_id).where(
                        Membership.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        assert paths == (independent if operation == "delete" else admitted)
        external = dict(
            (
                await session.execute(
                    select(ExternalUser.user_id, ExternalUser.active).where(
                        ExternalUser.organization_id == cohort.org_id
                    )
                )
            )
            .tuples()
            .all()
        )
        assert external == {
            uid: not (deprovisioned and uid == cohort.users[0]) for uid in cohort.users
        }
        # Deprovisioning preserves identities; it never disables the global account.
        assert set(
            (
                await session.scalars(
                    select(User.__table__.c.id).where(
                        User.__table__.c.id.in_(cohort.users),
                        User.__table__.c.is_active.is_(True),
                    )
                )
            ).all()
        ) == set(cohort.users)


@pytest.mark.parametrize("operation", ["remove_member", "delete", "unmap"])
@pytest.mark.parametrize("deprovision_order", ["never", "before", "after"])
async def test_group_changes_and_user_deprovisioning_have_distinct_effects(
    cohort: Cohort,
    mapped_directory: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    operation: str,
    deprovision_order: str,
) -> None:
    async with AsyncSession(cohort.engine, expire_on_commit=False) as session:
        service = SCIMService(session, cohort.role)
        if deprovision_order == "before":
            await service.deprovision_user(cohort.users[0])
            await session.commit()
        await change_directory_group(cohort, mapped_directory[0], operation, session)
        if deprovision_order == "after":
            # In the unmap case this must remove the newly retained manual row too.
            await service.deprovision_user(cohort.users[0])
            await session.commit()
    await assert_directory_outcome(
        cohort, mapped_directory, operation, deprovision_order != "never"
    )
    if deprovision_order != "never":
        async with AsyncSession(cohort.engine, expire_on_commit=False) as session:
            service = SCIMService(session, cohort.role)
            external = await session.get(ExternalUser, cohort.external[0])
            assert external is not None
            await service.reactivate_external_user(external)
            await session.commit()
        async with AsyncSession(cohort.engine) as session:
            assert (
                await session.get(
                    OrganizationMembership, (cohort.users[0], cohort.org_id)
                )
                is not None
            )
            # Every tested operation removed this user's source path; reactivation
            # must not resurrect old direct or retained/manual paths.
            assert (
                await session.scalars(
                    select(Membership.user_id).where(
                        Membership.workspace_id == mapped_directory[1],
                        Membership.user_id == cohort.users[0],
                    )
                )
            ).all() == []


@pytest.mark.parametrize("operation", ["delete", "unmap"])
@pytest.mark.parametrize("deprovision_first", [False, True])
async def test_group_removal_racing_deprovision_never_retains_offboarded_user(
    cohort: Cohort,
    mapped_directory: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    deprovision_first: bool,
) -> None:
    async def deprovision(session: AsyncSession) -> None:
        await SCIMService(session, cohort.role).deprovision_user(cohort.users[0])
        await session.commit()

    async def group_change(session: AsyncSession) -> None:
        await change_directory_group(cohort, mapped_directory[0], operation, session)

    await interleave(
        cohort,
        monkeypatch,
        deprovision if deprovision_first else group_change,
        group_change if deprovision_first else deprovision,
    )
    await assert_directory_outcome(cohort, mapped_directory, operation, True)
