"""Batch case-row hydration shared by case details and search pages."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from asyncpg.exceptions import UndefinedTableError
from psycopg.errors import UndefinedTable as PsycopgUndefinedTable
from sqlalchemy import exc as sa_exc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.db.models import CaseTableRow, Table
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TablesService


def _is_missing_table(error: sa_exc.DBAPIError) -> bool:
    """Recognize PostgreSQL undefined-table errors through driver wrappers."""
    current: BaseException | None = error.orig
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        # Both drivers map SQLSTATE 42P01 to an explicit exception type.
        if isinstance(current, UndefinedTableError | PsycopgUndefinedTable):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


class CaseRowHydrator(BaseWorkspaceService):
    """Load workspace-owned linked rows without importing the case service."""

    service_name = "case_row_hydrator"

    def __init__(
        self, session: AsyncSession, role: Role, *, tables: TablesService | None = None
    ):
        super().__init__(session, role)
        self.tables = tables if tables is not None else TablesService(session, role)

    async def hydrate_case_rows(
        self,
        *,
        case_ids: list[uuid.UUID],
        include_row_data: bool,
    ) -> dict[uuid.UUID, list[CaseTableRowRead]]:
        if not case_ids:
            return {}
        stmt = (
            select(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id.in_(case_ids),
            )
            .order_by(CaseTableRow.created_at.desc(), CaseTableRow.id.desc())
        )
        links = (await self.session.execute(stmt)).scalars().all()
        hydrated = await self._hydrate_links(links, include_row_data=include_row_data)
        grouped: dict[uuid.UUID, list[CaseTableRowRead]] = defaultdict(list)
        for row in hydrated:
            grouped[row.case_id].append(row)
        return grouped

    async def _hydrate_links(
        self,
        links: Sequence[CaseTableRow],
        *,
        include_row_data: bool,
    ) -> list[CaseTableRowRead]:
        tables_by_id = await self._get_tables_by_id([link.table_id for link in links])
        rows_by_table_id: dict[uuid.UUID, dict[uuid.UUID, dict[str, Any]]] = {}
        if include_row_data:
            row_ids_by_table_id: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
            for link in links:
                if link.table_id in tables_by_id:
                    row_ids_by_table_id[link.table_id].add(link.row_id)

            for table_id, row_ids in row_ids_by_table_id.items():
                try:
                    # A physical table can disappear while its case links remain.
                    # Roll back that read before hydrating later tables or fields.
                    async with self.session.begin_nested():
                        rows_by_table_id[table_id] = await self.tables.get_rows(
                            tables_by_id[table_id], list(row_ids)
                        )
                except sa_exc.DBAPIError as exc:
                    if not _is_missing_table(exc):
                        raise

        hydrated: list[CaseTableRowRead] = []

        for link in links:
            table = tables_by_id.get(link.table_id)
            row_data: dict[str, Any] | None = None
            is_available = False
            if include_row_data and table is not None:
                row_data = rows_by_table_id.get(link.table_id, {}).get(link.row_id)
                is_available = row_data is not None
            elif not include_row_data:
                is_available = True

            hydrated.append(
                CaseTableRowRead(
                    id=link.id,
                    case_id=link.case_id,
                    table_id=link.table_id,
                    table_name=table.name if table else None,
                    row_id=link.row_id,
                    row_data=row_data,
                    is_row_available=is_available,
                    created_at=link.created_at,
                    updated_at=link.updated_at,
                )
            )

        return hydrated

    async def _get_tables_by_id(
        self, table_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Table]:
        if not table_ids:
            return {}
        stmt = select(Table).where(
            Table.workspace_id == self.workspace_id,
            Table.id.in_(set(table_ids)),
        )
        tables = (await self.session.execute(stmt)).scalars().all()
        return {table.id: table for table in tables}
