import csv
from io import StringIO
from typing import Annotated, Any, Literal
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

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatImportError, TracecatNotFoundError
from tracecat.identifiers import TableColumnID, TableID
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.tables.enums import SqlType
from tracecat.tables.importer import CSVImporter
from tracecat.tables.schemas import (
    InferredColumn,
    TableColumnCreate,
    TableColumnRead,
    TableColumnUpdate,
    TableCreate,
    TableImportResponse,
    TableRead,
    TableReadMinimal,
    TableRowInsert,
    TableRowInsertBatch,
    TableRowInsertBatchResponse,
    TableRowRead,
    TableRowUpdate,
    TableUpdate,
)
from tracecat.tables.service import TablesService

router = APIRouter(prefix="/tables", tags=["tables"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]
WorkspaceEditorUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=[WorkspaceRole.ADMIN, WorkspaceRole.EDITOR],
    ),
]


async def _read_csv_upload_with_limit(file: UploadFile, *, max_size: int) -> bytes:
    """Read an uploaded CSV file enforcing a maximum size limit."""
    chunk_size = 1024 * 1024  # 1MB chunks to balance throughput and memory
    total_read = 0
    buffer = bytearray()

    await file.seek(0)
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > max_size:
            max_size_mb = max_size / (1024 * 1024)
            logger.warning(
                "CSV import exceeds size limit",
                filename=file.filename,
                declared_content_type=file.content_type,
                max_size_bytes=max_size,
            )
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"CSV file exceeds maximum allowed size of {max_size_mb:.2f}MB"
                ),
            )
        buffer.extend(chunk)

    if total_read == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file appears to be empty",
        )

    return bytes(buffer)


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
    role: WorkspaceEditorUser,
    session: AsyncDBSession,
    params: TableCreate,
) -> None:
    """Create a new table."""
    service = TablesService(session, role=role)
    try:
        await service.create_table(params)
    except ProgrammingError as e:
        # Drill down to the root cause
        while (cause := e.__cause__) is not None:
            e = cause
        if isinstance(e, DuplicateTableError):
            logger.warning(f"Duplicate table error: {e}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Table already exists",
            ) from e
        if isinstance(e, DuplicateColumnError):
            logger.warning(f"Duplicate column error: {e}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Column already exists",
            ) from e
        logger.error(f"Unexpected error creating table: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the table",
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

    # Get unique index info or default to empty dict if not present
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
                options=column.options,
            )
            for column in table.columns
        ],
    )


@router.patch("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_table(
    role: WorkspaceEditorUser,
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
    role: WorkspaceEditorUser,
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
    role: WorkspaceEditorUser,
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
    role: WorkspaceEditorUser,
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
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
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
    role: WorkspaceEditorUser,
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
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
    order_by: str | None = Query(default=None, description="Column name to order by"),
    sort: Literal["asc", "desc"] | None = Query(
        default=None, description="Sort direction (asc or desc)"
    ),
) -> CursorPaginatedResponse[TableRowRead]:
    """List table rows with cursor-based pagination and sorting."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    params = CursorPaginationParams(
        limit=limit,
        cursor=cursor,
        reverse=reverse,
    )

    try:
        response = await service.list_rows(table, params, order_by=order_by, sort=sort)
    except ValueError as e:
        logger.warning(f"Invalid request for list rows: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list rows: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve rows",
        ) from e

    # Convert the response items to TableRowRead format
    return CursorPaginatedResponse(
        items=[TableRowRead.model_validate(row) for row in response.items],
        next_cursor=response.next_cursor,
        prev_cursor=response.prev_cursor,
        has_more=response.has_more,
        has_previous=response.has_previous,
    )


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
    role: WorkspaceEditorUser,
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


@router.patch("/{table_id}/rows/{row_id}")
async def update_row(
    role: WorkspaceEditorUser,
    session: AsyncDBSession,
    table_id: TableID,
    row_id: UUID,
    params: TableRowUpdate,
) -> TableRowRead:
    """Update a row in a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    try:
        row = await service.update_row(table, row_id, params.data)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except DBAPIError as e:
        logger.exception("Database error occurred during row update")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A database error occurred. Please check your input and try again.",
        ) from e
    return TableRowRead.model_validate(row)


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
        logger.exception("Database error occurred during batch row insert")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A database error occurred. Please check your input and try again.",
        ) from e


async def get_column_mapping(column_mapping: str = Form(...)) -> dict[str, str]:
    try:
        return orjson.loads(column_mapping)
    except orjson.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON format for column_mapping",
        ) from e


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_table_from_csv(
    role: WorkspaceEditorUser,
    session: AsyncDBSession,
    file: UploadFile = File(...),
    table_name: str | None = Form(default=None),
) -> TableImportResponse:
    """Create a new table by importing a CSV file."""
    service = TablesService(session, role=role)

    try:
        contents = await _read_csv_upload_with_limit(
            file, max_size=config.TRACECAT__MAX_TABLE_IMPORT_SIZE_BYTES
        )
        table, rows_inserted, inferred_columns = await service.import_table_from_csv(
            contents=contents,
            filename=file.filename,
            table_name=table_name,
        )
        table_with_columns = await service.get_table(table.id)
        index_columns = await service.get_index(table_with_columns)
        table_read = TableRead(
            id=table_with_columns.id,
            name=table_with_columns.name,
            columns=[
                TableColumnRead(
                    id=column.id,
                    name=column.name,
                    type=SqlType(column.type),
                    nullable=column.nullable,
                    default=column.default,
                    is_index=column.name in index_columns,
                    options=column.options,
                )
                for column in table_with_columns.columns
            ],
        )
        return TableImportResponse(
            table=table_read,
            rows_inserted=rows_inserted,
            column_mapping=[
                InferredColumn(
                    csv_header=column.original_name,
                    field_name=column.name,
                    field_type=column.type,
                )
                for column in inferred_columns
            ],
        )
    except HTTPException:
        raise
    except TracecatImportError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except Exception as e:
        logger.warning(f"Unexpected error during table import: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error processing CSV: {str(e)}",
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
    csv_file: StringIO | None = None
    try:
        contents = await _read_csv_upload_with_limit(
            file, max_size=config.TRACECAT__MAX_TABLE_IMPORT_SIZE_BYTES
        )
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
    except HTTPException:
        raise
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
        if csv_file is not None:
            csv_file.close()

    return TableRowInsertBatchResponse(rows_inserted=importer.total_rows_inserted)
