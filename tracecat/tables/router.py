from typing import Annotated
from uuid import UUID

from asyncpg import DuplicateColumnError, DuplicateTableError
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import ProgrammingError

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import TableColumnID, TableID
from tracecat.logger import logger
from tracecat.tables.models import (
    TableColumnCreate,
    TableColumnRead,
    TableColumnUpdate,
    TableCreate,
    TableRead,
    TableReadMinimal,
    TableRowInsert,
    TableRowRead,
    TableUpdate,
)
from tracecat.tables.service import TablesService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/tables", tags=["tables"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]
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
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[TableReadMinimal]:
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
    try:
        table = await service.create_table(params)
    except ProgrammingError as e:
        # Drill down to the root cause
        while (cause := e.__cause__) is not None:
            e = cause
        if isinstance(e, DuplicateTableError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e).replace("relation", "table").capitalize(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error occurred: {e}",
        ) from e

    for col_params in params.columns:
        try:
            await service.create_column(table, col_params)
        except ProgrammingError as e:
            # Drill down to the root cause
            while (cause := e.__cause__) is not None:
                e = cause
            if isinstance(e, DuplicateColumnError):
                # Format: 'column "field>" of relation "<table>" already exists'
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(e).replace("relation", "table").capitalize(),
                ) from e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error occurred: {e}",
            ) from e


@router.get("/{table_id}", response_model=TableRead)
async def get_table(
    role: WorkspaceUser,
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

    return TableRead(
        id=table.id,
        name=table.name,
        columns=[
            TableColumnRead.model_validate(column, from_attributes=True)
            for column in table.columns
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
    try:
        await service.update_table(table, params)
    except ProgrammingError as e:
        # Drill down to the root cause
        while (cause := e.__cause__) is not None:
            e = cause
        if isinstance(e, DuplicateTableError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e).replace("relation", "table").capitalize(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected DB error occurred: {e}",
        ) from e


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
async def create_column(
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
    try:
        await service.create_column(table, params)
    except ProgrammingError as e:
        # Drill down to the root cause
        while (cause := e.__cause__) is not None:
            e = cause
        if isinstance(e, DuplicateColumnError):
            # Format: 'column "field>" of relation "<table>" already exists'
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e).replace("relation", "table").capitalize(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error occurred: {e}",
        ) from e


@router.patch(
    "/{table_id}/columns/{column_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_column(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    column_id: TableColumnID,
    params: TableColumnUpdate,
) -> None:
    """Update a column."""
    service = TablesService(session, role=role)
    try:
        column = await service.get_column(table_id, column_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    try:
        await service.update_column(column, params)
    except ProgrammingError as e:
        # Drill down to the root cause
        while (cause := e.__cause__) is not None:
            e = cause
        if isinstance(e, DuplicateColumnError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e).replace("relation", "table").capitalize(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error occurred: {e}",
        ) from e


@router.delete(
    "/{table_id}/columns/{column_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_column(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    column_id: TableColumnID,
) -> None:
    """Delete a column from a table."""
    service = TablesService(session, role=role)
    try:
        column = await service.get_column(table_id, column_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.delete_column(column)


@router.get("/{table_id}/rows")
async def list_rows(
    role: WorkspaceUser,
    session: AsyncDBSession,
    table_id: TableID,
    limit: int = Query(default=100, description="Maximum number of rows to return"),
    offset: int = Query(default=0, description="Number of rows to skip"),
) -> list[TableRowRead]:
    """Get a row by ID."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    rows = await service.list_rows(table, limit=limit, offset=offset)
    return [TableRowRead.model_validate(row) for row in rows]


@router.get("/{table_id}/rows/{row_id}")
async def get_row(
    role: WorkspaceUser,
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
async def insert_row(
    role: WorkspaceUser,
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


@router.delete("/{table_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    table_id: TableID,
    row_id: UUID,
) -> None:
    """Delete a row from a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.delete_row(table, row_id)
