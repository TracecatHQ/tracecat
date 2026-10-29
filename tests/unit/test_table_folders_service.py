from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Table, TableFolder
from tracecat.exceptions import ScopeDeniedError, TracecatValidationError
from tracecat.tables.folders.schemas import (
    TableDirectoryItem,
    TableFolderDirectoryItem,
)
from tracecat.tables.folders.service import TableFolderService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def folder_service(
    session: AsyncSession, svc_role: Role
) -> AsyncGenerator[TableFolderService, None]:
    yield TableFolderService(session=session, role=svc_role)


def _table(service: TableFolderService, name: str, folder_id=None) -> Table:
    return Table(workspace_id=service.workspace_id, name=name, folder_id=folder_id)


@pytest.mark.anyio
async def test_folder_reads_require_table_read_scope() -> None:
    service = TableFolderService(
        session=AsyncSession(),
        role=Role(
            type="user",
            user_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            service_id="tracecat-api",
            scopes=frozenset(),
        ),
    )
    with pytest.raises(ScopeDeniedError) as exc_info:
        await service.get_directory_items("/")
    assert exc_info.value.missing_scopes == ["table:read"]


@pytest.mark.anyio
async def test_directory_lists_folders_then_tables_by_name(
    folder_service: TableFolderService,
) -> None:
    parent = await folder_service.create_folder(name="detection", parent_path="/")
    await folder_service.create_folder(name="nested", parent_path="/detection/")
    folder_service.session.add_all(
        [
            _table(folder_service, "zeta_root"),
            _table(folder_service, "alpha_root"),
            _table(folder_service, "inside", folder_id=parent.id),
        ]
    )
    await folder_service.session.commit()

    root_items = await folder_service.get_directory_items("/")
    assert [(item.type, item.name) for item in root_items] == [
        ("folder", "detection"),
        ("table", "alpha_root"),
        ("table", "zeta_root"),
    ]
    folder_item = root_items[0]
    assert isinstance(folder_item, TableFolderDirectoryItem)
    assert folder_item.num_items == 2

    nested_items = await folder_service.get_directory_items("/detection/")
    assert [(item.type, item.name) for item in nested_items] == [
        ("folder", "nested"),
        ("table", "inside"),
    ]
    assert isinstance(nested_items[1], TableDirectoryItem)
    assert nested_items[1].folder_id == parent.id


@pytest.mark.anyio
async def test_move_table_between_folder_and_root(
    folder_service: TableFolderService,
) -> None:
    folder = await folder_service.create_folder(name="slack", parent_path="/")
    table = _table(folder_service, "slack_events")
    folder_service.session.add(table)
    await folder_service.session.commit()

    moved = await folder_service.move_table(table.id, folder)
    assert moved.folder_id == folder.id
    assert moved.name == "slack_events"

    moved = await folder_service.move_table(table.id, None)
    assert moved.folder_id is None


@pytest.mark.anyio
async def test_rename_and_move_folder_rewrite_descendant_paths(
    folder_service: TableFolderService,
) -> None:
    parent = await folder_service.create_folder(name="parent", parent_path="/")
    child = await folder_service.create_folder(name="child", parent_path="/parent/")
    await folder_service.create_folder(name="leaf", parent_path="/parent/child/")
    target = await folder_service.create_folder(name="target", parent_path="/")
    child_id, target_id = child.id, target.id

    renamed = await folder_service.rename_folder(parent.id, "renamed")
    assert renamed.path == "/renamed/"
    # Each API request uses a fresh session; drop rows cached before the rename.
    folder_service.session.expire_all()

    moved = await folder_service.move_folder(child_id, target_id)
    assert moved.path == "/target/child/"
    result = await folder_service.session.execute(
        select(TableFolder.path).where(
            TableFolder.workspace_id == folder_service.workspace_id
        )
    )
    assert set(result.scalars().all()) == {
        "/renamed/",
        "/target/",
        "/target/child/",
        "/target/child/leaf/",
    }

    with pytest.raises(TracecatValidationError):
        await folder_service.move_folder(target_id, child_id)


@pytest.mark.anyio
async def test_delete_folder_never_deletes_tables(
    folder_service: TableFolderService,
) -> None:
    parent = await folder_service.create_folder(name="parent", parent_path="/")
    child = await folder_service.create_folder(name="child", parent_path="/parent/")
    folder_service.session.add_all(
        [
            _table(folder_service, "parent_table", folder_id=parent.id),
            _table(folder_service, "child_table", folder_id=child.id),
        ]
    )
    await folder_service.session.commit()

    with pytest.raises(TracecatValidationError):
        await folder_service.delete_folder(parent.id)

    await folder_service.delete_folder(parent.id, recursive=True)

    folders = await folder_service.session.execute(
        select(TableFolder.path).where(
            TableFolder.workspace_id == folder_service.workspace_id
        )
    )
    tables = await folder_service.session.execute(
        select(Table.name, Table.folder_id).where(
            Table.workspace_id == folder_service.workspace_id
        )
    )
    assert folders.scalars().all() == []
    assert dict(tables.tuples().all()) == {"parent_table": None, "child_table": None}
