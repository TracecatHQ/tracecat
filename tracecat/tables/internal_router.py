"""Internal router for table operations (SDK/UDF use)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import orjson
from asyncpg import DuplicateTableError
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from pydantic_core import to_jsonable_python
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.expressions.functions import tabulate
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.tables.common import coerce_optional_to_utc_datetime
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import (
    TableAggregation,
    TableColumnCreate,
    TableColumnRead,
    TableCreate,
    TableLookupResponse,
    TableRead,
    TableRowInsert,
    TableRowInsertBatch,
)
from tracecat.tables.service import TablesService

router = APIRouter(
    prefix="/internal/tables", tags=["internal-tables"], include_in_schema=False
)


class TableCreateRequest(BaseModel):
    name: str
    columns: list[TableColumnCreate] = Field(default_factory=list)
    raise_on_duplicate: bool = True


class TableLookupRequest(BaseModel):
    columns: list[str]
    values: list[Any]
    limit: int | None = Field(
        default=None,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    )
    group_by: str | None = None
    agg: TableAggregation | None = None
    agg_field: str | None = None

    @model_validator(mode="after")
    def validate_aggregation(self) -> TableLookupRequest:
        has_group_by = self.group_by is not None
        has_agg = self.agg is not None
        has_agg_field = self.agg_field is not None

        if not has_agg and (has_group_by or has_agg_field):
            raise ValueError("group_by and agg_field require agg")
        if has_agg and self.agg is TableAggregation.VALUE_COUNTS and not has_group_by:
            raise ValueError("value_counts aggregation requires group_by")

        return self


class TableExistsRequest(BaseModel):
    columns: list[str]
    values: list[Any]


class TableSearchRequest(BaseModel):
    search_term: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    updated_before: datetime | None = None
    updated_after: datetime | None = None
    cursor: str | None = None
    reverse: bool = False
    limit: int = Field(
        default=config.TRACECAT__LIMIT_TABLE_SEARCH_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    )


class TableRowUpdate(BaseModel):
    data: dict[str, Any]


TableDownloadFormat = Literal["json", "ndjson", "csv", "markdown"]


@router.get("")
@require_scope("table:read")
async def list_tables(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> list[dict[str, Any]]:
    """List all tables for the workspace."""
    service = TablesService(session, role=role)
    tables = await service.list_tables()
    return [table.to_dict() for table in tables]


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("table:create")
async def create_table(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: TableCreateRequest,
) -> dict[str, Any]:
    """Create a new table."""
    service = TablesService(session, role=role)
    try:
        table = await service.create_table(
            TableCreate(name=params.name, columns=params.columns)
        )
    except ProgrammingError as exc:
        # Drill down to the root cause
        while (cause := exc.__cause__) is not None:
            exc = cause
        if isinstance(exc, DuplicateTableError):
            if params.raise_on_duplicate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Table already exists",
                ) from exc
            await session.rollback()
            table = await service.get_table_by_name(params.name)
        else:
            logger.error("Unexpected error creating table", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while creating the table",
            ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return table.to_dict()


@router.get("/{table_name}/metadata", response_model=TableRead)
@require_scope("table:read")
async def get_table_metadata(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
) -> TableRead:
    """Get table metadata by name."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    index_columns = await service.get_index(table)
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


@router.post("/{table_name}/lookup")
@require_scope("table:read")
async def lookup_rows(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    params: TableLookupRequest,
) -> TableLookupResponse:
    """Lookup rows matching column/value pairs."""
    if params.limit is not None and params.limit > config.TRACECAT__LIMIT_CURSOR_MAX:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}"
            ),
        )
    service = TablesService(session, role=role)
    try:
        result = await service.lookup_rows(
            table_name,
            columns=params.columns,
            values=params.values,
            limit=params.limit,
            group_by=params.group_by,
            agg=params.agg,
            agg_field=params.agg_field,
        )
        if isinstance(result, list):
            return TableLookupResponse(items=result, aggregation=None)
        return result
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{table_name}/exists")
@require_scope("table:read")
async def exists_rows(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    params: TableExistsRequest,
) -> bool:
    """Check if any rows match column/value pairs."""
    service = TablesService(session, role=role)
    try:
        return await service.exists_rows(
            table_name,
            columns=params.columns,
            values=params.values,
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{table_name}/search")
@require_scope("table:read")
async def search_rows(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    params: TableSearchRequest,
) -> CursorPaginatedResponse[dict[str, Any]]:
    """Search rows in a table with optional filters."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    try:
        return await service.list_rows(
            table,
            params=CursorPaginationParams(
                limit=params.limit,
                cursor=params.cursor,
                reverse=params.reverse,
            ),
            search_term=params.search_term,
            start_time=coerce_optional_to_utc_datetime(params.start_time),
            end_time=coerce_optional_to_utc_datetime(params.end_time),
            updated_before=coerce_optional_to_utc_datetime(params.updated_before),
            updated_after=coerce_optional_to_utc_datetime(params.updated_after),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{table_name}/rows", status_code=status.HTTP_201_CREATED)
@require_scope("table:create")
async def insert_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    params: TableRowInsert,
) -> dict[str, Any]:
    """Insert a row into a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    try:
        return await service.insert_row(table, params)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/{table_name}/rows/batch", status_code=status.HTTP_201_CREATED)
@require_scope("table:create")
async def insert_rows_batch(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    params: TableRowInsertBatch,
) -> int:
    """Insert multiple rows into a table atomically."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    try:
        return await service.batch_insert_rows(table, params.rows, upsert=params.upsert)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except DBAPIError as exc:
        detail = str(exc.__cause__ or exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database error: {detail}",
        ) from exc


@router.patch("/{table_name}/rows/{row_id}")
@require_scope("table:update")
async def update_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    row_id: UUID,
    params: TableRowUpdate,
) -> dict[str, Any]:
    """Update a row in a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    try:
        return await service.update_row(table, row_id=row_id, data=params.data)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/{table_name}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("table:delete")
async def delete_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    row_id: UUID,
) -> None:
    """Delete a row from a table."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    try:
        await service.delete_row(table, row_id=row_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/{table_name}/download")
@require_scope("table:read")
async def download_table(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_name: str,
    format: TableDownloadFormat | None = Query(default=None),
    limit: int = Query(
        default=config.TRACECAT__LIMIT_TABLE_DOWNLOAD_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX,
    ),
) -> list[dict[str, Any]] | str:
    """Download table data as JSON, NDJSON, CSV, or Markdown."""
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(table_name)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # Download uses cursor pagination in chunks and is capped by download max.
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(rows) < limit:
        page_size = min(limit - len(rows), config.TRACECAT__LIMIT_CURSOR_MAX)
        rows_page = await service.list_rows(
            table=table,
            params=CursorPaginationParams(
                limit=page_size, cursor=cursor, reverse=False
            ),
        )
        rows.extend(rows_page.items)
        if not rows_page.has_more or rows_page.next_cursor is None:
            break
        cursor = rows_page.next_cursor
    json_safe_rows = to_jsonable_python(rows, fallback=str)

    if format is None:
        return json_safe_rows
    if format == "json":
        return orjson.dumps(json_safe_rows).decode()
    if format == "ndjson":
        return "\n".join([orjson.dumps(row).decode() for row in json_safe_rows])
    if format in {"csv", "markdown"}:
        return tabulate(json_safe_rows, format)
    return tabulate(json_safe_rows, format)
