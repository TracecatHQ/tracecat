"""Regression tests for organization-scoped workspace creation limits."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import DropSchema

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import (
    Organization,
    OrganizationTier,
    Ownership,
    Tier,
    Workspace,
)
from tracecat.exceptions import EntitlementRequired
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.enums import Entitlement
from tracecat.workspaces.schemas import WorkspaceUpdate
from tracecat.workspaces.service import WorkspaceService

pytestmark = pytest.mark.usefixtures("db")


async def _set_transaction_timeouts(session: AsyncSession) -> None:
    """Keep lock and statement failures bounded in independent test sessions."""
    await session.execute(text("SET LOCAL lock_timeout = '30s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5min'"))


def _service_role(organization_id: uuid.UUID) -> Role:
    """Return an internal role with the workspace management scopes needed here."""
    return Role(
        type="service",
        service_id="tracecat-service",
        organization_id=organization_id,
        scopes=frozenset({"*"}),
    )


@dataclass(slots=True)
class WorkspaceDatabase:
    """Track committed synthetic organizations and isolate concurrent sessions."""

    session_factory: async_sessionmaker[AsyncSession]
    organization_ids: list[uuid.UUID] = field(default_factory=list)

    async def create_organization(self) -> uuid.UUID:
        """Create and commit an organization visible to all test connections."""
        organization_id = uuid.uuid4()
        async with self.session_factory() as session:
            await _set_transaction_timeouts(session)
            session.add(
                Organization(
                    id=organization_id,
                    name=f"Workspace entitlement org {organization_id.hex[:8]}",
                    slug=f"workspace-entitlement-{organization_id.hex}",
                    is_active=True,
                )
            )
            await session.commit()
        self.organization_ids.append(organization_id)
        return organization_id

    async def add_workspace(self, organization_id: uuid.UUID, name: str) -> uuid.UUID:
        """Seed an existing workspace row without invoking creation admission."""
        workspace_id = uuid.uuid4()
        async with self.session_factory() as session:
            await _set_transaction_timeouts(session)
            session.add(
                Workspace(
                    id=workspace_id,
                    name=name,
                    organization_id=organization_id,
                )
            )
            session.add(
                Ownership(
                    resource_id=str(workspace_id),
                    resource_type="workspace",
                    owner_id=organization_id,
                    owner_type="user",
                )
            )
            await session.commit()
        return workspace_id

    async def assign_tier(
        self,
        organization_id: uuid.UUID,
        *,
        multi_workspace: bool,
        override: bool | None = None,
    ) -> None:
        """Assign a dedicated tier so hosted entitlement resolution is explicit."""
        async with self.session_factory() as session:
            await _set_transaction_timeouts(session)
            tier = Tier(
                display_name=f"Workspace tier {uuid.uuid4().hex[:8]}",
                entitlements={"multi_workspace": multi_workspace},
                is_default=False,
                is_active=True,
            )
            session.add(tier)
            await session.flush()
            session.add(
                OrganizationTier(
                    organization_id=organization_id,
                    tier_id=tier.id,
                    entitlement_overrides=(
                        {"multi_workspace": override} if override is not None else None
                    ),
                )
            )
            await session.commit()

    async def count_workspaces(self, organization_id: uuid.UUID) -> int:
        """Read the committed workspace count for one organization."""
        async with self.session_factory() as session:
            await _set_transaction_timeouts(session)
            count = await session.scalar(
                select(func.count(Workspace.id)).where(
                    Workspace.organization_id == organization_id
                )
            )
            return int(count or 0)

    async def cleanup(self) -> None:
        """Drop dynamic case schemas and remove only this fixture's rows."""
        if not self.organization_ids:
            return

        async with self.session_factory() as session:
            await _set_transaction_timeouts(session)
            workspace_ids = list(
                (
                    await session.scalars(
                        select(Workspace.id).where(
                            Workspace.organization_id.in_(self.organization_ids)
                        )
                    )
                ).all()
            )
            for workspace_id in workspace_ids:
                schema_name = f"custom_fields_{WorkspaceUUID.new(workspace_id).short()}"
                await session.execute(
                    DropSchema(schema_name, cascade=True, if_exists=True)
                )

            await session.execute(
                delete(Ownership).where(Ownership.owner_id.in_(self.organization_ids))
            )
            await session.execute(
                delete(Workspace).where(
                    Workspace.organization_id.in_(self.organization_ids)
                )
            )
            await session.execute(
                delete(Organization).where(Organization.id.in_(self.organization_ids))
            )
            await session.commit()


@pytest.fixture
async def workspace_db(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[WorkspaceDatabase, None]:
    """Provide a committed database and independent READ COMMITTED sessions."""
    # Admission tests must use production OSS defaults, not the permissive
    # entitlement baseline used by unrelated suites to provision test workspaces.
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.resolve_oss_default_entitlements(None),
    )
    engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        isolation_level="READ COMMITTED",
        poolclass=NullPool,
    )
    workspace_db = WorkspaceDatabase(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield workspace_db
    finally:
        await workspace_db.cleanup()
        await engine.dispose()


async def _create_workspace(
    workspace_db: WorkspaceDatabase,
    organization_id: uuid.UUID,
    name: str,
) -> Workspace:
    """Create a workspace in a fresh session for real admission behavior."""
    async with workspace_db.session_factory() as session:
        await _set_transaction_timeouts(session)
        service = WorkspaceService(session, role=_service_role(organization_id))
        return await service.create_workspace(name)


async def _ensure_default_workspace(
    workspace_db: WorkspaceDatabase, organization_id: uuid.UUID
) -> None:
    """Run default workspace bootstrap in a fresh session."""
    async with workspace_db.session_factory() as session:
        await _set_transaction_timeouts(session)
        service = WorkspaceService(session, role=_service_role(organization_id))
        await service.ensure_default_workspace()


@pytest.mark.anyio
async def test_single_tenant_allows_first_workspace_and_rejects_second(
    workspace_db: WorkspaceDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSS creates its first workspace and gates later workspace creation."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    organization_id = await workspace_db.create_organization()

    first = await _create_workspace(workspace_db, organization_id, "first")

    with pytest.raises(EntitlementRequired) as exc_info:
        await _create_workspace(workspace_db, organization_id, "second")

    assert first.name == "first"
    assert exc_info.value.entitlement == Entitlement.MULTI_WORKSPACE.value
    assert await workspace_db.count_workspaces(organization_id) == 1


@pytest.mark.anyio
async def test_existing_workspaces_remain_readable_updatable_and_creation_is_denied(
    workspace_db: WorkspaceDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing OSS workspaces remain usable after the creation gate is added."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    organization_id = await workspace_db.create_organization()
    first_id = await workspace_db.add_workspace(organization_id, "first")
    second_id = await workspace_db.add_workspace(organization_id, "second")

    async with workspace_db.session_factory() as session:
        await _set_transaction_timeouts(session)
        service = WorkspaceService(session, role=_service_role(organization_id))

        accessible = await service.list_accessible_workspaces()
        assert {workspace.id for workspace in accessible} == {first_id, second_id}

        first = await service.get_workspace(first_id)
        assert first is not None
        updated = await service.update_workspace(first, WorkspaceUpdate(name="renamed"))
        assert updated.name == "renamed"

        with pytest.raises(EntitlementRequired) as exc_info:
            await service.create_workspace("third")

    assert exc_info.value.entitlement == Entitlement.MULTI_WORKSPACE.value
    assert await workspace_db.count_workspaces(organization_id) == 2


@pytest.mark.anyio
async def test_workspace_existence_check_is_scoped_to_the_current_organization(
    workspace_db: WorkspaceDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace in another organization must not consume this org's first slot."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    first_organization_id = await workspace_db.create_organization()
    other_organization_id = await workspace_db.create_organization()
    await workspace_db.add_workspace(other_organization_id, "other-org-workspace")

    created = await _create_workspace(
        workspace_db, first_organization_id, "first-org-workspace"
    )

    assert created.organization_id == first_organization_id
    assert await workspace_db.count_workspaces(first_organization_id) == 1
    assert await workspace_db.count_workspaces(other_organization_id) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tier_value", "override_value", "allowed"),
    [
        (True, None, True),
        (False, None, False),
        (False, True, True),
        (True, False, False),
    ],
)
async def test_hosted_tier_and_override_control_additional_workspace_creation(
    workspace_db: WorkspaceDatabase,
    monkeypatch: pytest.MonkeyPatch,
    tier_value: bool,
    override_value: bool | None,
    allowed: bool,
) -> None:
    """Hosted tiers and per-org overrides determine the second workspace decision."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    organization_id = await workspace_db.create_organization()
    await workspace_db.add_workspace(organization_id, "existing")
    await workspace_db.assign_tier(
        organization_id,
        multi_workspace=tier_value,
        override=override_value,
    )

    if allowed:
        created = await _create_workspace(workspace_db, organization_id, "additional")
        assert created.name == "additional"
    else:
        with pytest.raises(EntitlementRequired) as exc_info:
            await _create_workspace(workspace_db, organization_id, "additional")
        assert exc_info.value.entitlement == Entitlement.MULTI_WORKSPACE.value

    assert await workspace_db.count_workspaces(organization_id) == (2 if allowed else 1)


@pytest.mark.anyio
async def test_concurrent_different_name_creation_has_one_success_and_one_denial(
    workspace_db: WorkspaceDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The organization lock prevents two fresh OSS requests from both bypassing the gate."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    organization_id = await workspace_db.create_organization()

    results = await asyncio.gather(
        _create_workspace(workspace_db, organization_id, "candidate-a"),
        _create_workspace(workspace_db, organization_id, "candidate-b"),
        return_exceptions=True,
    )

    successes = [result for result in results if isinstance(result, Workspace)]
    denials = [result for result in results if isinstance(result, EntitlementRequired)]
    unexpected = [
        result
        for result in results
        if isinstance(result, BaseException)
        and not isinstance(result, EntitlementRequired)
    ]

    assert not unexpected
    assert len(successes) == 1
    assert len(denials) == 1
    assert successes[0].name in {"candidate-a", "candidate-b"}
    assert denials[0].entitlement == Entitlement.MULTI_WORKSPACE.value
    assert await workspace_db.count_workspaces(organization_id) == 1


@pytest.mark.anyio
async def test_concurrent_default_workspace_bootstrap_is_idempotent(
    workspace_db: WorkspaceDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent bootstrap creates one workspace even before a hosted tier exists."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    organization_id = await workspace_db.create_organization()

    results = await asyncio.gather(
        _ensure_default_workspace(workspace_db, organization_id),
        _ensure_default_workspace(workspace_db, organization_id),
        return_exceptions=True,
    )

    assert results == [None, None]
    assert await workspace_db.count_workspaces(organization_id) == 1
