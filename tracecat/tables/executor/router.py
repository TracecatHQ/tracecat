from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import TableColumnID, TableID
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.tables.common import coerce_optional_to_utc_datetime
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnRead,
    TableColumnUpdate,
    TableCreate,
    TableRead,
    TableReadMinimal,
    TableRowInsert,
    TableRowInsertBatch,
    TableRowInsertBatchResponse,
    TableRowsSearchParams,
    TableRowUpdate,
    TableUpdate,
)
from tracecat.tables.service import TablesService

router = APIRouter(
    prefix="/internal/tables", tags=["internal-tables"], include_in_schema=False
)


class ExistsRowsResponse(BaseModel):
    exists: bool


@router.get("")
async def list_tables(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> list[TableReadMinimal]:
    service = TablesService(session, role=role)
    tables = await service.list_tables()
    return [
        TableReadMinimal.model_validate(table, from_attributes=True) for table in tables
    ]


@router.get("/by-name/{name}", response_model=TableRead)
async def get_table_by_name(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    name: str,
) -> TableRead:
    service = TablesService(session, role=role)
    try:
        table = await service.get_table_by_name(name)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

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


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TableRead)
async def create_table(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: TableCreate,
) -> TableRead:
    service = TablesService(session, role=role)
    await service.create_table(params)
    table = await service.get_table_by_name(params.name)
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


@router.get("/{table_id}", response_model=TableRead)
async def get_table(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
) -> TableRead:
    service = TablesService(session, role=role)
    try:
        table = await service.get_table(table_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
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


@router.patch("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_table(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableUpdate,
) -> None:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    await service.update_table(table, params)


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
) -> None:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    await service.delete_table(table)


@router.post(
    "/{table_id}/columns",
    status_code=status.HTTP_201_CREATED,
    response_model=TableColumnRead,
)
async def create_column(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableColumnCreate,
) -> TableColumnRead:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    column = await service.create_column(table, params)
    return TableColumnRead(
        id=column.id,
        name=column.name,
        type=SqlType(column.type),
        nullable=column.nullable,
        default=column.default,
        is_index=False,
        options=column.options,
    )


@router.patch("/{table_id}/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_column(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    column_id: TableColumnID,
    params: TableColumnUpdate,
) -> None:
    service = TablesService(session, role=role)
    column = await service.get_column(table_id, column_id)
    await service.update_column(column, params)


@router.delete(
    "/{table_id}/columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_column(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    column_id: TableColumnID,
) -> None:
    service = TablesService(session, role=role)
    column = await service.get_column(table_id, column_id)
    await service.delete_column(column)


@router.get("/{table_id}/rows")
async def list_rows(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
    order_by: str | None = Query(default=None, description="Column name to order by"),
    sort: Literal["asc", "desc"] | None = Query(
        default=None, description="Sort direction (asc or desc)"
    ),
) -> CursorPaginatedResponse[dict[str, Any]]:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    params = CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
    response = await service.list_rows_paginated(
        table, params, order_by=order_by, sort=sort
    )
    return response


@router.get("/{table_id}/rows/{row_id}")
async def get_row(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    row_id: UUID,
) -> dict[str, Any]:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    return await service.get_row(table, row_id)


@router.post("/{table_id}/rows", status_code=status.HTTP_201_CREATED)
async def insert_row(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableRowInsert,
) -> dict[str, Any]:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    return await service.insert_row(table, params)


@router.post("/{table_id}/rows/batch", status_code=status.HTTP_201_CREATED)
async def batch_insert_rows(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableRowInsertBatch,
) -> TableRowInsertBatchResponse:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    rows_inserted = await service.batch_insert_rows(
        table, params.rows, upsert=params.upsert
    )
    return TableRowInsertBatchResponse(rows_inserted=rows_inserted)


@router.delete("/{table_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    row_id: UUID,
) -> None:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    await service.delete_row(table, row_id)


@router.patch("/{table_id}/rows/{row_id}")
async def update_row(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    row_id: UUID,
    params: TableRowUpdate,
) -> dict[str, Any]:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    try:
        return await service.update_row(table, row_id, params.data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post("/{table_id}/rows/search")
async def search_rows(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    params: TableRowsSearchParams,
) -> list[dict[str, Any]]:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    try:
        return await service.search_rows(
            table=table,
            search_term=params.search_term,
            start_time=coerce_optional_to_utc_datetime(params.start_time),
            end_time=coerce_optional_to_utc_datetime(params.end_time),
            updated_before=coerce_optional_to_utc_datetime(params.updated_before),
            updated_after=coerce_optional_to_utc_datetime(params.updated_after),
            limit=params.limit,
            offset=params.offset,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get("/{table_id}/download")
async def download_table(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: TableID,
    limit: int = Query(default=1000, ge=1, le=1000),
) -> list[dict[str, Any]]:
    service = TablesService(session, role=role)
    table = await service.get_table(table_id)
    return await service.list_rows(table=table, limit=limit)


@router.get("/lookup")
async def lookup_rows(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table: str = Query(..., description="Table name"),
    column: str = Query(..., description="Column to search"),
    value: str = Query(..., description="Value to match"),
    limit: int = Query(default=1, ge=1, le=1000, description="Maximum rows to return"),
) -> list[dict[str, Any]]:
    service = TablesService(session, role=role)
    try:
        return await service.lookup_rows(
            table,
            columns=[column],
            values=[value],
            limit=limit,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get("/exists", response_model=ExistsRowsResponse)
async def exists_rows(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table: str = Query(..., description="Table name"),
    column: str = Query(..., description="Column to search"),
    value: str = Query(..., description="Value to check"),
) -> ExistsRowsResponse:
    service = TablesService(session, role=role)
    try:
        exists = await service.exists_rows(table, columns=[column], values=[value])
        return ExistsRowsResponse(exists=exists)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get("/lookup-many")
async def lookup_many_rows(
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table: str = Query(..., description="Table name"),
    column: str = Query(..., description="Column to search"),
    values: list[str] = Query(..., description="Values to match (OR logic)"),
    limit: int | None = Query(
        default=None, ge=1, le=1000, description="Maximum rows to return"
    ),
) -> list[dict[str, Any]]:
    service = TablesService(session, role=role)
    all_rows: list[dict[str, Any]] = []
    for value in values:
        rows = await service.lookup_rows(
            table,
            columns=[column],
            values=[value],
            limit=limit,
        )
        all_rows.extend(rows)
        if limit is not None and len(all_rows) >= limit:
            return all_rows[:limit]
    return all_rows
