"""Column mutations must not trigger implicit async relationship loads."""

from collections.abc import Iterator
from typing import Literal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableColumnUpdate, TableCreate
from tracecat.tables.service import TablesService

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """These PostgreSQL regressions do not use object storage."""
    yield


@pytest.mark.parametrize("parent_state", ["expired", "absent"])
@pytest.mark.parametrize(
    "operation", ["rename", "create_index", "drop_index", "delete"]
)
async def test_mutate_column_with_unloaded_table(
    session: AsyncSession,
    svc_admin_role: Role,
    parent_state: Literal["expired", "absent"],
    operation: Literal["rename", "create_index", "drop_index", "delete"],
) -> None:
    service = TablesService(session, svc_admin_role)
    table = await service.create_table(
        TableCreate(
            name="column_mutation",
            columns=[
                TableColumnCreate(
                    name="value", type=SqlType.TEXT, is_index=operation == "drop_index"
                )
            ],
        )
    )
    table_id = table.id
    session.expunge_all()

    # The internal router selects the column from table.columns, rather than
    # querying TableColumn directly with its select-in parent relationship.
    table = await service.get_table(table_id)
    column = next(c for c in table.columns if c.name == "value")
    assert "table" in sa.inspect(column).unloaded
    if parent_state == "expired":
        session.expire(table, ["name"])
    else:
        session.expunge(table)
        # Table.columns cascades expunge to its members; reattach just the column.
        session.add(column)

    if operation == "delete":
        await service.delete_column(column)
    elif operation == "rename":
        await service.update_column(column, TableColumnUpdate(name="renamed"))
    else:
        await service.update_column(
            column, TableColumnUpdate(is_index=operation == "create_index")
        )

    table = await service.get_table(table_id, populate_existing=True)
    expected_names = (
        []
        if operation == "delete"
        else ["renamed" if operation == "rename" else "value"]
    )
    assert [c.name for c in table.columns] == expected_names
    assert await service.get_index(table) == (
        ["value"] if operation == "create_index" else []
    )

    conn = await session.connection()
    physical_names = await conn.run_sync(
        lambda sync_conn: [
            c["name"]
            for c in sa.inspect(sync_conn).get_columns(
                table.name,
                schema=f"tables_{WorkspaceUUID(str(table.workspace_id)).short()}",
            )
        ]
    )
    assert ("value" in physical_names) == ("value" in expected_names)
    assert ("renamed" in physical_names) == ("renamed" in expected_names)
