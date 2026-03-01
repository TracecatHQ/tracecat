from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from asyncpg.exceptions import UndefinedTableError
from sqlalchemy import exc as sa_exc
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import (
    CaseTableRowInsertCreate,
    CaseTableRowLinkCreate,
    CaseTableRowRead,
)
from tracecat.cases.schemas import TableRowLinkedEvent, TableRowUnlinkedEvent
from tracecat.cases.service import CaseEventsService
from tracecat.db.models import Case, CaseTableRow, Table
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import BaseCursorPaginator, CursorPaginatedResponse
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TablesService

MAX_LINKED_ROWS_PER_CASE = 200
MAX_TABLES_PER_CASE = 10


class CaseTableRowsService(BaseWorkspaceService):
    service_name = "case_table_rows"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.tables = TablesService(session=self.session, role=self.role)

    async def list_rows(
        self,
        *,
        case_id: uuid.UUID,
        limit: int,
        cursor: str | None = None,
        reverse: bool = False,
        include_row_data: bool = True,
    ) -> CursorPaginatedResponse[CaseTableRowRead]:
        paginator = BaseCursorPaginator(self.session)
        stmt = (
            select(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case_id,
            )
            .options(selectinload(CaseTableRow.case))
            .order_by(CaseTableRow.created_at.desc(), CaseTableRow.id.desc())
        )

        if cursor:
            cursor_data = paginator.decode_cursor(cursor)
            cursor_id = uuid.UUID(cursor_data.id)
            if reverse:
                stmt = stmt.where(CaseTableRow.id > cursor_id)
                stmt = stmt.order_by(CaseTableRow.id.asc())
            else:
                stmt = stmt.where(CaseTableRow.id < cursor_id)

        stmt = stmt.limit(limit + 1)
        result = await self.session.execute(stmt)
        links = result.scalars().all()

        has_more = len(links) > limit
        items = links[:limit] if has_more else links
        has_previous = cursor is not None

        if reverse:
            items = list(reversed(items))

        hydrated = await self._hydrate_links(items, include_row_data=include_row_data)

        next_cursor = None
        prev_cursor = None
        if items and has_more:
            next_cursor = paginator.encode_cursor(items[-1].id)
        if items and cursor:
            prev_cursor = paginator.encode_cursor(items[0].id)

        if reverse:
            next_cursor, prev_cursor = prev_cursor, next_cursor
            has_more, has_previous = has_previous, has_more

        return CursorPaginatedResponse(
            items=hydrated,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
        )

    async def link_row(
        self, *, case: Case, params: CaseTableRowLinkCreate
    ) -> CaseTableRow:
        table = await self.tables.get_table(params.table_id)
        await self.tables.get_row(table, params.row_id)

        dedupe_stmt = select(CaseTableRow).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == params.table_id,
            CaseTableRow.row_id == params.row_id,
        )
        existing = (await self.session.execute(dedupe_stmt)).scalars().first()
        if existing is not None:
            return existing

        total_links_stmt = (
            select(func.count())
            .select_from(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case.id,
            )
        )
        total_links = int((await self.session.scalar(total_links_stmt)) or 0)
        if total_links >= MAX_LINKED_ROWS_PER_CASE:
            raise ValueError(
                f"A case can have at most {MAX_LINKED_ROWS_PER_CASE} linked rows"
            )

        distinct_tables_stmt = select(
            func.count(sa.distinct(CaseTableRow.table_id))
        ).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case.id,
        )
        distinct_tables = int((await self.session.scalar(distinct_tables_stmt)) or 0)
        if distinct_tables >= MAX_TABLES_PER_CASE:
            raise ValueError(
                f"A case can link rows from at most {MAX_TABLES_PER_CASE} tables"
            )

        link = CaseTableRow(
            workspace_id=self.workspace_id,
            case_id=case.id,
            table_id=table.id,
            row_id=params.row_id,
        )
        self.session.add(link)

        await CaseEventsService(self.session, self.role).create_event(
            case,
            TableRowLinkedEvent(
                table_id=table.id, table_name=table.name, row_id=params.row_id
            ),
        )
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def unlink_row(
        self, *, case: Case, table_id: uuid.UUID, row_id: uuid.UUID
    ) -> bool:
        stmt = select(CaseTableRow).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == table_id,
            CaseTableRow.row_id == row_id,
        )
        link = (await self.session.execute(stmt)).scalars().first()
        if link is None:
            return False

        table_name = None
        table_stmt = select(Table).where(
            Table.workspace_id == self.workspace_id,
            Table.id == table_id,
        )
        table = (await self.session.execute(table_stmt)).scalars().first()
        if table is not None:
            table_name = table.name

        await self.session.delete(link)

        await CaseEventsService(self.session, self.role).create_event(
            case,
            TableRowUnlinkedEvent(
                table_id=table_id, table_name=table_name, row_id=row_id
            ),
        )
        await self.session.commit()
        return True

    async def insert_row_to_case(
        self,
        *,
        case: Case,
        params: CaseTableRowInsertCreate,
    ) -> CaseTableRow:
        table = await self.tables.get_table(params.table_id)
        row = await self.tables.insert_row(table, params.row)
        row_id = row.get("id")
        if not isinstance(row_id, uuid.UUID):
            raise ValueError("Inserted row ID is invalid")
        return await self.link_row(
            case=case,
            params=CaseTableRowLinkCreate(table_id=params.table_id, row_id=row_id),
        )

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
        hydrated: list[CaseTableRowRead] = []

        for link in links:
            table = tables_by_id.get(link.table_id)
            row_data: dict[str, Any] | None = None
            is_available = False
            if include_row_data and table is not None:
                try:
                    row_data = await self.tables.get_row(table, link.row_id)
                    is_available = True
                except TracecatNotFoundError:
                    is_available = False
                except sa_exc.DBAPIError as exc:
                    if not isinstance(exc.orig, UndefinedTableError):
                        raise
                    is_available = False
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

    async def get_case_or_raise(self, case_id: uuid.UUID) -> Case:
        stmt = select(Case).where(
            Case.workspace_id == self.workspace_id,
            Case.id == case_id,
        )
        case = (await self.session.execute(stmt)).scalars().first()
        if case is None:
            raise TracecatNotFoundError(f"Case {case_id} not found")
        return case
