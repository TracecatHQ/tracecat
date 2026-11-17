import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.managed_resources import mark_managed
from tracecat.auth.types import Role
from tracecat.db.models import Table, TableFolder
from tracecat.managed_resources.guard import (
    ManagedResourceLockedError,
    managed_write_bypass,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.folders.service import TableFolderService
from tracecat.tables.schemas import TableColumnCreate, TableCreate
from tracecat.tables.service import BaseTablesService
from tracecat.workspace_sync.adapters.base import SyncMappingService
from tracecat.workspace_sync.adapters.table import TableAdapter
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import TableResourceSpec, WorkspaceSpec

pytestmark = pytest.mark.usefixtures("db")


class _SyncService(SyncMappingService):
    service_name = "workspace_sync_test"


@pytest.mark.anyio
async def test_table_folder_path_round_trips_through_git_sync(
    session: AsyncSession, svc_role: Role
) -> None:
    tables = BaseTablesService(session=session, role=svc_role)
    folders = TableFolderService(session=session, role=svc_role)
    sync = _SyncService(session=session, role=svc_role)
    adapter = TableAdapter()

    nested = await tables.create_table(
        TableCreate(
            name="folder_sync_nested",
            columns=[TableColumnCreate(name="value", type=SqlType.TEXT)],
        )
    )
    await tables.create_table(TableCreate(name="folder_sync_root"))
    await folders.create_folder(name="detection", parent_path="/")
    leaf = await folders.create_folder(name="findings", parent_path="/detection/")
    await folders.move_table(nested.id, leaf)

    projection = await adapter.project(sync)
    specs = {
        spec.name: spec
        for spec in projection.specs.values()
        if isinstance(spec, TableResourceSpec)
    }
    assert specs["folder_sync_nested"].folder_path == "/detection/findings/"
    assert specs["folder_sync_root"].folder_path is None
    assert "folder_path" not in specs["folder_sync_root"].model_dump(exclude_none=True)

    # Pull a spec that moves the nested table into a folder that does not exist yet
    # and moves the root table into an existing folder.
    nested_id, root_id = (
        source_id
        for name in ("folder_sync_nested", "folder_sync_root")
        for source_id, spec in projection.specs.items()
        if isinstance(spec, TableResourceSpec) and spec.name == name
    )
    pulled = WorkspaceSpec(
        tables={
            nested_id: specs["folder_sync_nested"].model_copy(
                update={"folder_path": "/threat-hunting/ips/"}
            ),
            root_id: specs["folder_sync_root"].model_copy(
                update={"folder_path": "/detection/"}
            ),
        }
    )
    await adapter.import_specs(sync, pulled)
    await session.commit()

    rows = await session.execute(
        select(Table.name, TableFolder.path)
        .outerjoin(TableFolder, Table.folder_id == TableFolder.id)
        .where(Table.workspace_id == sync.workspace_id)
    )
    assert dict(rows.tuples().all()) == {
        "folder_sync_nested": "/threat-hunting/ips/",
        "folder_sync_root": "/detection/",
    }

    # Omitting folder_path in Git moves the table back to the workspace root.
    await adapter.import_specs(
        sync,
        WorkspaceSpec(
            tables={
                nested_id: specs["folder_sync_nested"].model_copy(
                    update={"folder_path": None}
                )
            }
        ),
    )
    await session.commit()
    folder_id = await session.scalar(
        select(Table.folder_id).where(
            Table.workspace_id == sync.workspace_id,
            Table.name == "folder_sync_nested",
        )
    )
    assert folder_id is None


@pytest.mark.anyio
async def test_pull_cannot_move_a_managed_table(
    session: AsyncSession, svc_role: Role
) -> None:
    """A customer pull adopts a table by name, but only a rollout may move a managed one."""
    tables = BaseTablesService(session=session, role=svc_role)
    sync = _SyncService(session=session, role=svc_role)
    adapter = TableAdapter()
    table = await tables.create_table(TableCreate(name="managed_sync_table"))
    table_id = table.id
    await mark_managed(session, svc_role, SyncResourceType.TABLE, table_id)
    await session.commit()

    moved = WorkspaceSpec(
        tables={
            "managed-src": TableResourceSpec(
                id="managed-src", name="managed_sync_table", folder_path="/elsewhere/"
            )
        }
    )
    with pytest.raises(ManagedResourceLockedError):
        await adapter.import_specs(sync, moved)
    await session.rollback()

    # An unchanged folder needs no write, so the pull still succeeds.
    unchanged = WorkspaceSpec(
        tables={
            "managed-src": TableResourceSpec(
                id="managed-src", name="managed_sync_table"
            )
        }
    )
    await adapter.import_specs(sync, unchanged)
    await session.rollback()

    with managed_write_bypass():
        await adapter.import_specs(sync, moved)
    await session.commit()
    path = await session.scalar(
        select(TableFolder.path)
        .join(Table, Table.folder_id == TableFolder.id)
        .where(Table.id == table_id)
    )
    assert path == "/elsewhere/"
