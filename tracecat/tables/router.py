from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import TableColumnID, TableID
from tracecat.logger import logger
from tracecat.tables.models import (
    TableColumnCreate,
    TableColumnRead,
    TableCreate,
    TableRead,
    TableReadMinimal,
    TableRowInsert,
    TableUpdate,
)
from tracecat.tables.service import TablesService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/tables", tags=["tables"])

WorkspaceAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=AccessLevel.ADMIN,
    ),
]


@router.get("")
async def list_tables(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
) -> Any:
    """List all tables."""
    service = TablesService(session, role=role)
    tables = await service.list_tables()
    return [
        TableReadMinimal.model_validate(table, from_attributes=True) for table in tables
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_table(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    params: TableCreate,
) -> None:
    """Create a new table."""
    service = TablesService(session, role=role)
    await service.create_table(params)


@router.get("/{table_id}", response_model=TableRead)
async def get_table(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
) -> TableRead:
    """Get a table by ID."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    cols = table.columns
    return TableRead(
        id=table.id,
        name=table.name,
        columns=[
            TableColumnRead.model_validate(column, from_attributes=True)
            for column in cols
        ],
    )


@router.patch("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_table(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableUpdate,
) -> None:
    """Update table metadata."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.update_table(table, params)


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
) -> None:
    """Delete a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.delete_table(table)


@router.post("/{table_id}/columns", status_code=status.HTTP_201_CREATED)
async def create_table_column(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableColumnCreate,
) -> None:
    """Add a column to a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.create_column(table, params)


@router.delete(
    "/{table_id}/columns/{column_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_table_column(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    column_id: TableColumnID,
) -> None:
    """Delete a column from a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    try:
        column = await service.get_column(table, column_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.delete_column(column)


"""Row operaitons.

Listing all rows is the same as reading the table.
"""


@router.get("/{table_id}/rows/{row_id}")
async def get_table_row(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    row_id: UUID,
) -> None:
    """Get a row by ID."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    row = await service.get_row(table, row_id)
    logger.info(f"Row: {row}")
    return row


@router.post("/{table_id}/rows", status_code=status.HTTP_201_CREATED)
async def insert_table_row(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableRowInsert,
) -> None:
    """Create a new row in a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.insert_row(table, params)
