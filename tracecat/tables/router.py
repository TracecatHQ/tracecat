import csv
from io import StringIO
from typing import Annotated, Any
from uuid import UUID

import orjson
from asyncpg import DuplicateColumnError, DuplicateTableError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import TableColumnID, TableID
from tracecat.logger import logger
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import CSVImporter
from tracecat.tables.models import (
    TableColumnCreate,
    TableColumnRead,
    TableColumnUpdate,
    TableCreate,
    TableRead,
    TableReadMinimal,
    TableRowInsert,
    TableRowInsertBatch,
    TableRowInsertBatchResponse,
    TableRowRead,
    TableUpdate,
)
from tracecat.tables.service import TablesService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatImportError, TracecatNotFoundError

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

    # Get natural key info or default to empty dict if not present
    index_columns = await service.get_index(table)

    # Convert to response model (includes is_index field)
    return TableRead(
        id=table.id,
        name=table.name,
        columns=[
            TableColumnRead(
                id=column.id,
                name=column.name,
                type=SqlType(column.type),
                nullable=column.nullable,
                default=column.default,
                is_index=column.name in index_columns,
            )
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
        await service.insert_row(table, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        if "duplicate values" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate value detected in a unique column. Please use a different value.",
            ) from e
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e


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


@router.post("/{table_id}/rows/batch", status_code=status.HTTP_201_CREATED)
async def batch_insert_rows(
    role: WorkspaceUser,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableRowInsertBatch,
) -> TableRowInsertBatchResponse:
    """Insert multiple rows into a table atomically.

    All rows will be inserted in a single transaction. If any row fails,
    the entire batch will be rolled back.
    """
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    try:
        count = await service.batch_insert_rows(table, params.rows)
        return TableRowInsertBatchResponse(rows_inserted=count)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except DBAPIError as e:
        # Extract useful info from database error
        detail = str(e)
        if isinstance(e.__cause__, Exception):
            detail = str(e.__cause__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database error: {detail}",
        ) from e


async def get_column_mapping(column_mapping: str = Form(...)) -> dict[str, str]:
    try:
        return orjson.loads(column_mapping)
    except orjson.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON format for column_mapping",
        ) from e


@router.post("/{table_id}/import", status_code=status.HTTP_201_CREATED)
async def import_csv(
    role: WorkspaceUser,
    session: AsyncDBSession,
    table_id: TableID,
    file: UploadFile = File(...),
    column_mapping: dict[str, str] = Depends(get_column_mapping),
) -> TableRowInsertBatchResponse:
    """Import data from a CSV file into a table."""
    service = TablesService(session, role=role)

    # Get table info
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    # Initialize import service
    importer = CSVImporter(table.columns)

    # Process CSV file
    try:
        contents = await file.read()
        csv_file = StringIO(contents.decode())
        csv_reader = csv.DictReader(csv_file)

        current_chunk: list[dict[str, Any]] = []

        # Process rows in chunks
        for row in csv_reader:
            mapped_row = importer.map_row(row, column_mapping)
            current_chunk.append(mapped_row)

            if len(current_chunk) >= importer.chunk_size:
                await importer.process_chunk(current_chunk, service, table)
                current_chunk = []

        # Process remaining rows
        await importer.process_chunk(current_chunk, service, table)
    except TracecatImportError as e:
        logger.warning(f"Error during import: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.warning(f"Unexpected error during import: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error processing CSV: {str(e)}",
        ) from e
    finally:
        csv_file.close()

    return TableRowInsertBatchResponse(rows_inserted=importer.total_rows_inserted)
