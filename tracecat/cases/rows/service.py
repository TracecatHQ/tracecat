"""Service for managing case table rows."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from asyncpg.exceptions import UndefinedTableError
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import tracecat.cases.service as cases_service
from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import CaseTableRowLink, CaseTableRowRead
from tracecat.cases.schemas import TableRowLinkedEvent, TableRowUnlinkedEvent
from tracecat.contexts import ctx_run
from tracecat.db.models import Case, CaseTableRow
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.logger import logger
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TablesService

MAX_CASE_ROW_LINKS = 50


@dataclass(frozen=True)
class _CaseRowLinkSnapshot:
    id: uuid.UUID
    case_id: uuid.UUID
    table_id: uuid.UUID
    row_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


def _is_missing_table_error(exc: ProgrammingError) -> bool:
    root_exc: BaseException = exc
    while (cause := root_exc.__cause__) is not None:
        root_exc = cause
    return isinstance(root_exc, UndefinedTableError)


class CaseTableRowService(BaseWorkspaceService):
    """Service for managing case table rows."""

    service_name = "case_table_rows"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.tables_service = TablesService(session=session, role=role)
        self.events_service = cases_service.CaseEventsService(
            session=session, role=role
        )

    async def _serialize_links(
        self, links: Sequence[CaseTableRow]
    ) -> list[CaseTableRowRead]:
        if not links:
            return []

        links_by_table: dict[uuid.UUID, list[_CaseRowLinkSnapshot]] = defaultdict(list)
        table_name_by_id: dict[uuid.UUID, str] = {}
        snapshots: list[_CaseRowLinkSnapshot] = []
        for link in links:
            snapshot = _CaseRowLinkSnapshot(
                id=link.id,
                case_id=link.case_id,
                table_id=link.table_id,
                row_id=link.row_id,
                created_at=link.created_at,
                updated_at=link.updated_at,
            )
            snapshots.append(snapshot)
            links_by_table[snapshot.table_id].append(snapshot)
            if link.table is not None:
                table_name_by_id[snapshot.table_id] = link.table.name

        row_data_by_table: dict[uuid.UUID, dict[uuid.UUID, dict[str, Any]]] = {}
        for table_id, table_links in links_by_table.items():
            try:
                table = await self.tables_service.get_table(table_id)
                table_name_by_id[table_id] = table.name
                row_ids = [table_link.row_id for table_link in table_links]
                try:
                    async with self.session.begin_nested():
                        row_data_by_table[
                            table_id
                        ] = await self.tables_service.get_rows_by_ids(table, row_ids)
                except ProgrammingError as exc:
                    if not _is_missing_table_error(exc):
                        raise
                    logger.warning(
                        "Failed to load linked case table rows",
                        table_id=table_id,
                        error=str(exc),
                    )
                    row_data_by_table[table_id] = {}
            except TracecatNotFoundError as exc:
                logger.warning(
                    "Failed to load linked case table rows",
                    table_id=table_id,
                    error=str(exc),
                )
                row_data_by_table[table_id] = {}

        serialized: list[CaseTableRowRead] = []
        for snapshot in snapshots:
            table_name = table_name_by_id.get(snapshot.table_id, "<unknown table>")
            row_data = row_data_by_table.get(snapshot.table_id, {}).get(
                snapshot.row_id, {}
            )
            serialized.append(
                CaseTableRowRead(
                    id=snapshot.id,
                    case_id=snapshot.case_id,
                    table_id=snapshot.table_id,
                    row_id=snapshot.row_id,
                    table_name=table_name,
                    row_data=row_data,
                    created_at=snapshot.created_at,
                    updated_at=snapshot.updated_at,
                )
            )
        return serialized

    async def list_case_table_rows(
        self, case: Case, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[CaseTableRowRead]:
        """List paginated table rows for a case."""
        stmt = (
            sa.select(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case.id,
            )
            .options(selectinload(CaseTableRow.table))
            .order_by(CaseTableRow.created_at.desc(), CaseTableRow.id.desc())
        )

        if params.cursor:
            try:
                cursor_data = BaseCursorPaginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as exc:
                raise ValueError(f"Invalid cursor: {exc}") from exc

            cursor_created_at: datetime | None = None
            if cursor_data.sort_column == "created_at" and isinstance(
                cursor_data.sort_value, datetime
            ):
                cursor_created_at = cursor_data.sort_value

            if cursor_created_at is not None:
                if params.reverse:
                    stmt = stmt.where(
                        sa.or_(
                            CaseTableRow.created_at > cursor_created_at,
                            sa.and_(
                                CaseTableRow.created_at == cursor_created_at,
                                CaseTableRow.id > cursor_id,
                            ),
                        )
                    )
                else:
                    stmt = stmt.where(
                        sa.or_(
                            CaseTableRow.created_at < cursor_created_at,
                            sa.and_(
                                CaseTableRow.created_at == cursor_created_at,
                                CaseTableRow.id < cursor_id,
                            ),
                        )
                    )

        stmt = stmt.limit(params.limit + 1)
        result = await self.session.execute(stmt)
        all_links = result.scalars().all()

        has_more = len(all_links) > params.limit
        links = all_links[: params.limit] if has_more else all_links
        items = await self._serialize_links(links)

        next_cursor = None
        prev_cursor = None
        has_previous = params.cursor is not None
        if links:
            if has_more:
                last_link = links[-1]
                next_cursor = BaseCursorPaginator.encode_cursor(
                    last_link.id,
                    sort_column="created_at",
                    sort_value=last_link.created_at,
                )
            if params.cursor:
                first_link = links[0]
                prev_cursor = BaseCursorPaginator.encode_cursor(
                    first_link.id,
                    sort_column="created_at",
                    sort_value=first_link.created_at,
                )

        if params.reverse:
            items = list(reversed(items))
            next_cursor, prev_cursor = prev_cursor, next_cursor
            has_more, has_previous = has_previous, has_more

        return CursorPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=None,
        )

    async def list_case_rows_for_case(self, case: Case) -> list[CaseTableRowRead]:
        rows_by_case = await self.list_case_rows_for_cases([case.id])
        return rows_by_case.get(case.id, [])

    async def list_case_rows_for_cases(
        self, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[CaseTableRowRead]]:
        if not case_ids:
            return {}

        stmt = (
            sa.select(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id.in_(case_ids),
            )
            .options(selectinload(CaseTableRow.table))
            .order_by(CaseTableRow.created_at.desc(), CaseTableRow.id.desc())
        )
        result = await self.session.execute(stmt)
        links = result.scalars().all()

        rows = await self._serialize_links(links)
        rows_by_case: dict[uuid.UUID, list[CaseTableRowRead]] = {
            case_id: [] for case_id in case_ids
        }
        for row in rows:
            rows_by_case[row.case_id].append(row)
        return rows_by_case

    async def get_case_table_row(
        self, case: Case, link_id: uuid.UUID
    ) -> CaseTableRowRead | None:
        stmt = (
            sa.select(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case.id,
                CaseTableRow.id == link_id,
            )
            .options(selectinload(CaseTableRow.table))
        )
        result = await self.session.execute(stmt)
        link = result.scalars().first()
        if link is None:
            return None

        serialized = await self._serialize_links([link])
        return serialized[0] if serialized else None

    async def get_link_by_table_row(
        self, case: Case, table_id: uuid.UUID, row_id: uuid.UUID
    ) -> CaseTableRow | None:
        stmt = sa.select(CaseTableRow).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == table_id,
            CaseTableRow.row_id == row_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def link_case_row(self, case: Case, params: CaseTableRowLink) -> CaseTableRow:
        """Link an existing table row to a case."""
        table = await self.tables_service.get_table(params.table_id)
        try:
            await self.tables_service.get_row(table, params.row_id)
        except TracecatNotFoundError as exc:
            raise TracecatNotFoundError(
                f"Row {params.row_id} not found in table {table.name}"
            ) from exc

        existing_link = await self.get_link_by_table_row(
            case, params.table_id, params.row_id
        )
        if existing_link is not None:
            return existing_link

        count_stmt = (
            sa.select(sa.func.count())
            .select_from(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case.id,
            )
        )
        existing_count = int(await self.session.scalar(count_stmt) or 0)
        if existing_count >= MAX_CASE_ROW_LINKS:
            raise TracecatValidationError(
                f"A case can have at most {MAX_CASE_ROW_LINKS} linked table rows"
            )

        run_ctx = ctx_run.get()
        link = CaseTableRow(
            workspace_id=self.workspace_id,
            case_id=case.id,
            table_id=params.table_id,
            row_id=params.row_id,
        )
        self.session.add(link)

        await self.events_service.create_event(
            case=case,
            event=TableRowLinkedEvent(
                table_id=params.table_id,
                row_id=params.row_id,
                table_name=table.name,
                wf_exec_id=run_ctx.wf_exec_id if run_ctx else None,
            ),
        )

        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def unlink_case_row(self, case: Case, case_table_row: CaseTableRow) -> None:
        """Unlink a table row association from a case."""
        if case_table_row.workspace_id != self.workspace_id:
            raise TracecatNotFoundError("Case table row not found")

        table_name = "<unknown table>"
        try:
            table = await self.tables_service.get_table(case_table_row.table_id)
            table_name = table.name
        except TracecatNotFoundError:
            logger.warning(
                "Case row table metadata missing during unlink event",
                table_id=case_table_row.table_id,
                link_id=case_table_row.id,
            )

        run_ctx = ctx_run.get()
        await self.events_service.create_event(
            case=case,
            event=TableRowUnlinkedEvent(
                table_id=case_table_row.table_id,
                row_id=case_table_row.row_id,
                table_name=table_name,
                wf_exec_id=run_ctx.wf_exec_id if run_ctx else None,
            ),
        )
        await self.session.delete(case_table_row)
        await self.session.commit()

    async def add_case_rows(self, case: Case, params: CaseTableRowLink) -> CaseTableRow:
        """Backward-compatible alias for link_case_row."""
        return await self.link_case_row(case, params)

    async def delete_case_rows(self, case: Case, case_table_row: CaseTableRow) -> None:
        """Backward-compatible alias for unlink_case_row."""
        await self.unlink_case_row(case, case_table_row)
