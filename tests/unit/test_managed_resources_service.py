import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    ManagedReferenceMatch,
    ManagedResource,
    ManagedTarget,
    Organization,
    Workspace,
)
from tracecat.managed_resources.enums import ManagedReferenceKind, ManagedTargetStatus
from tracecat.managed_resources.service import ManagedResourceService
from tracecat.workspace_sync.enums import SyncResourceType

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


def _managed_resource(
    workspace_id: uuid.UUID,
    local_id: uuid.UUID,
    resource_type: SyncResourceType = SyncResourceType.WORKFLOW,
    source_id: str | None = None,
) -> ManagedResource:
    return ManagedResource(
        workspace_id=workspace_id,
        resource_type=resource_type,
        local_id=local_id,
        source_id=source_id or f"source-{local_id.hex[:8]}",
        source_path=f"workflows/{local_id.hex[:8]}.yml",
        deployed_tag="v1.0.0",
        deployed_commit_sha="a" * 40,
    )


@pytest.fixture
async def service(session: AsyncSession, svc_role: Role) -> ManagedResourceService:
    return ManagedResourceService(session=session, role=svc_role)


async def test_get_managed_resource_returns_row(
    session: AsyncSession, svc_workspace: Workspace, service: ManagedResourceService
) -> None:
    local_id = uuid.uuid4()
    session.add(_managed_resource(svc_workspace.id, local_id))
    await session.flush()

    row = await service.get_managed_resource(SyncResourceType.WORKFLOW, local_id)

    assert row is not None
    assert row.local_id == local_id
    assert row.resource_type == SyncResourceType.WORKFLOW
    assert row.deployed_tag == "v1.0.0"
    assert row.deployed_at is not None


async def test_get_managed_resource_returns_none_when_unmanaged(
    session: AsyncSession, svc_workspace: Workspace, service: ManagedResourceService
) -> None:
    local_id = uuid.uuid4()
    session.add(_managed_resource(svc_workspace.id, local_id))
    await session.flush()

    assert (
        await service.get_managed_resource(SyncResourceType.WORKFLOW, uuid.uuid4())
        is None
    )
    # Same local id under a different resource type is not managed.
    assert (
        await service.get_managed_resource(SyncResourceType.AGENT_PRESET, local_id)
        is None
    )


async def test_get_managed_resource_ignores_other_workspaces(
    session: AsyncSession,
    svc_organization: Organization,
    service: ManagedResourceService,
) -> None:
    other_workspace = Workspace(
        name="other-workspace", organization_id=svc_organization.id
    )
    session.add(other_workspace)
    await session.flush()
    local_id = uuid.uuid4()
    session.add(_managed_resource(other_workspace.id, local_id))
    await session.flush()

    assert (
        await service.get_managed_resource(SyncResourceType.WORKFLOW, local_id) is None
    )


async def test_get_managed_resources_returns_only_managed_ids(
    session: AsyncSession, svc_workspace: Workspace, service: ManagedResourceService
) -> None:
    managed_a, managed_b, unmanaged = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_type = uuid.uuid4()
    session.add_all(
        [
            _managed_resource(svc_workspace.id, managed_a),
            _managed_resource(svc_workspace.id, managed_b),
            _managed_resource(svc_workspace.id, other_type, SyncResourceType.TABLE),
        ]
    )
    await session.flush()

    rows = await service.get_managed_resources(
        SyncResourceType.WORKFLOW, [managed_a, managed_b, unmanaged, other_type]
    )

    assert set(rows) == {managed_a, managed_b}
    assert rows[managed_a].local_id == managed_a
    assert rows[managed_b].local_id == managed_b


async def test_get_managed_resources_empty_input(
    service: ManagedResourceService,
) -> None:
    assert await service.get_managed_resources(SyncResourceType.WORKFLOW, []) == {}


async def test_workspace_delete_cascades_to_managed_tables(
    session: AsyncSession, svc_organization: Organization
) -> None:
    workspace = Workspace(name="managed-target", organization_id=svc_organization.id)
    session.add(workspace)
    await session.flush()
    session.add_all(
        [
            _managed_resource(workspace.id, uuid.uuid4()),
            ManagedTarget(
                workspace_id=workspace.id,
                pinned_tag="v1.0.0",
                last_rollout_status=ManagedTargetStatus.UP_TO_DATE,
                last_applied_tag="v1.0.0",
            ),
            ManagedReferenceMatch(
                workspace_id=workspace.id,
                reference_kind=ManagedReferenceKind.SECRET_STORE,
                source_key="source-store",
                resolved_id=uuid.uuid4(),
            ),
        ]
    )
    await session.flush()

    await session.execute(delete(Workspace).where(Workspace.id == workspace.id))
    session.expunge_all()

    for model in (ManagedResource, ManagedTarget, ManagedReferenceMatch):
        count = await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.workspace_id == workspace.id)
        )
        assert count == 0, model.__tablename__


async def test_duplicate_source_id_in_workspace_is_rejected(
    session: AsyncSession, svc_workspace: Workspace
) -> None:
    session.add(_managed_resource(svc_workspace.id, uuid.uuid4(), source_id="dup"))
    await session.flush()

    # The savepoint keeps the session usable for fixture cleanup.
    with pytest.raises(IntegrityError, match="uq_managed_resource_source"):
        async with session.begin_nested():
            session.add(
                _managed_resource(svc_workspace.id, uuid.uuid4(), source_id="dup")
            )


async def test_second_target_for_workspace_is_rejected(
    session: AsyncSession, svc_workspace: Workspace
) -> None:
    target = ManagedTarget(workspace_id=svc_workspace.id, pinned_tag="v1.0.0")
    session.add(target)
    await session.flush()
    await session.refresh(target)
    assert target.last_rollout_status is ManagedTargetStatus.PENDING

    with pytest.raises(IntegrityError, match="uq_managed_target_workspace_id"):
        async with session.begin_nested():
            session.add(
                ManagedTarget(workspace_id=svc_workspace.id, pinned_tag="v2.0.0")
            )
