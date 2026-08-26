from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from asyncpg.exceptions import UndefinedTableError
from sqlalchemy import exc as sa_exc
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import (
    CaseLinkedTableRead,
    CaseTableRowBatchLinkResponse,
    CaseTableRowInsertCreate,
    CaseTableRowLinkCreate,
    CaseTableRowRead,
)
from tracecat.cases.schemas import TableRowLinkedEvent, TableRowUnlinkedEvent
from tracecat.cases.service import CaseEventsService
from tracecat.db.models import Case, CaseTableRow, Table
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import (
    CursorPaginatedResponse,
    PageParams,
    paginate,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tables.schemas import TableColumnRead
from tracecat.tables.service import TablesService

# Linked-row caps. The case page pages through every row linked to a case, so
# these caps bound how much link data one case can accumulate:
#   - MAX_LINKED_ROWS_PER_TABLE: rows of one table linked to one case.
#   - MAX_TABLES_PER_CASE: distinct tables linked to one case.
#   - MAX_LINKED_ROWS_PER_CASE: links on one case across all tables. It is the
#     product of the other two caps, so it only binds once every table is full.
MAX_LINKED_ROWS_PER_TABLE = 250
MAX_TABLES_PER_CASE = 10
MAX_LINKED_ROWS_PER_CASE = 2_500


class CaseTableRowsService(BaseWorkspaceService):
    service_name = "case_table_rows"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.tables = TablesService(session=self.session, role=self.role)

    async def _get_existing_link(
        self, *, case_id: uuid.UUID, table_id: uuid.UUID, row_id: uuid.UUID
    ) -> CaseTableRow | None:
        stmt = select(CaseTableRow).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case_id,
            CaseTableRow.table_id == table_id,
            CaseTableRow.row_id == row_id,
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _existing_link_or_raise(
        self,
        *,
        case_id: uuid.UUID,
        table_id: uuid.UUID,
        row_id: uuid.UUID,
        message: str,
    ) -> CaseTableRow:
        """Re-check for a concurrent insert of this link before rejecting it.

        A link that already exists adds nothing to the counts the caps guard, so
        a request that lost the race to create it is still satisfiable.
        """
        existing = await self._get_existing_link(
            case_id=case_id, table_id=table_id, row_id=row_id
        )
        if existing is not None:
            return existing
        raise ValueError(message)

    async def _lock_case(self, case_id: uuid.UUID) -> None:
        """Serialize concurrent link requests for the same case.

        The limit checks below read aggregate state, so without a lock two
        in-flight link requests can both observe pre-insert counts and exceed
        the case limits. The lock is released when this transaction commits or
        rolls back.
        """
        await self.session.execute(
            select(Case.id)
            .where(
                Case.workspace_id == self.workspace_id,
                Case.id == case_id,
            )
            .with_for_update()
        )

    async def _count_links(self, case_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case_id,
            )
        )
        return int((await self.session.scalar(stmt)) or 0)

    async def _count_distinct_tables(self, case_id: uuid.UUID) -> int:
        stmt = select(func.count(sa.distinct(CaseTableRow.table_id))).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case_id,
        )
        return int((await self.session.scalar(stmt)) or 0)

    async def _count_table_links(self, case_id: uuid.UUID, table_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case_id,
                CaseTableRow.table_id == table_id,
            )
        )
        return int((await self.session.scalar(stmt)) or 0)

    async def _link_cap_violation(
        self, case_id: uuid.UUID, table_id: uuid.UUID
    ) -> str | None:
        """First cap a new link from this table would break, or None.

        Call under the case lock: the checks read aggregate state, so the counts
        must not move between them and the insert they guard.
        """
        table_links = await self._count_table_links(case_id, table_id)
        if table_links >= MAX_LINKED_ROWS_PER_TABLE:
            return f"A case can link at most {MAX_LINKED_ROWS_PER_TABLE} rows from one table"

        total_links = await self._count_links(case_id)
        if total_links >= MAX_LINKED_ROWS_PER_CASE:
            return f"A case can have at most {MAX_LINKED_ROWS_PER_CASE} linked rows"

        if table_links == 0:
            distinct_tables = await self._count_distinct_tables(case_id)
            if distinct_tables >= MAX_TABLES_PER_CASE:
                return f"A case can link rows from at most {MAX_TABLES_PER_CASE} tables"

        return None

    async def list_rows(
        self,
        *,
        case_id: uuid.UUID,
        limit: int,
        cursor: str | None = None,
        reverse: bool = False,
        include_row_data: bool = True,
        table_id: uuid.UUID | None = None,
    ) -> CursorPaginatedResponse[CaseTableRowRead]:
        """List linked rows, with an exact total when filtered by table."""
        filters = [
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case_id,
        ]
        if table_id is not None:
            filters.append(CaseTableRow.table_id == table_id)

        stmt = (
            select(CaseTableRow)
            .where(*filters)
            .options(selectinload(CaseTableRow.case))
        )

        # ``reverse`` remains accepted at the service boundary for compatibility;
        # the opaque cursor now owns scan direction.
        page = await paginate(
            self.session,
            stmt,
            page=PageParams(limit=limit, cursor=cursor),
            order_by=(
                CaseTableRow.created_at.desc(),
                CaseTableRow.id.desc(),
            ),
        )
        hydrated = await self._hydrate_links(
            page.items,
            include_row_data=include_row_data,
        )

        total_estimate = None
        if table_id is not None:
            count_stmt = select(func.count()).select_from(CaseTableRow).where(*filters)
            total_estimate = int((await self.session.scalar(count_stmt)) or 0)

        return CursorPaginatedResponse(
            items=hydrated,
            next_cursor=page.next_cursor,
            prev_cursor=page.prev_cursor,
            has_more=page.has_more,
            has_previous=page.has_previous,
            total_estimate=total_estimate,
        )

    async def list_linked_tables(
        self, *, case_id: uuid.UUID
    ) -> list[CaseLinkedTableRead]:
        """List linked tables with link counts and columns, ordered by table name.

        Columns travel with the summary so a caller rendering linked rows does
        not need a second, table-scoped read per table.
        """
        stmt = (
            select(
                CaseTableRow.table_id,
                Table.name,
                func.count().label("row_count"),
            )
            .join(
                Table,
                onclause=sa.and_(
                    Table.id == CaseTableRow.table_id,
                    Table.workspace_id == CaseTableRow.workspace_id,
                ),
                isouter=True,
            )
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case_id,
            )
            .group_by(CaseTableRow.table_id, Table.name)
            .order_by(Table.name.asc().nulls_last(), CaseTableRow.table_id.asc())
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        tables_by_id = await self._get_tables_by_id(
            [table_id for table_id, _, _ in rows]
        )

        linked_tables: list[CaseLinkedTableRead] = []
        for table_id, table_name, row_count in rows:
            table = tables_by_id.get(table_id)
            columns = (
                []
                if table is None
                else [
                    TableColumnRead.model_validate(column, from_attributes=True)
                    for column in table.columns
                ]
            )
            linked_tables.append(
                CaseLinkedTableRead(
                    table_id=table_id,
                    table_name=table_name,
                    row_count=row_count,
                    columns=columns,
                )
            )
        return linked_tables

    async def link_row(
        self, *, case: Case, params: CaseTableRowLinkCreate
    ) -> CaseTableRow:
        table = await self.tables.get_table(params.table_id)
        await self.tables.get_row(table, params.row_id)

        existing = await self._get_existing_link(
            case_id=case.id,
            table_id=params.table_id,
            row_id=params.row_id,
        )
        if existing is not None:
            return existing

        await self._lock_case(case.id)

        if (message := await self._link_cap_violation(case.id, table.id)) is not None:
            return await self._existing_link_or_raise(
                case_id=case.id,
                table_id=table.id,
                row_id=params.row_id,
                message=message,
            )

        stmt = (
            insert(CaseTableRow)
            .values(
                workspace_id=self.workspace_id,
                case_id=case.id,
                table_id=table.id,
                row_id=params.row_id,
            )
            .on_conflict_do_nothing(constraint="uq_case_table_row_link")
            .returning(CaseTableRow.surrogate_id)
        )
        link_surrogate_id = await self.session.scalar(stmt)

        if link_surrogate_id is None:
            existing = await self._get_existing_link(
                case_id=case.id,
                table_id=table.id,
                row_id=params.row_id,
            )
            if existing is not None:
                return existing
            raise TracecatNotFoundError("Case row link was not created")

        link = await self.session.get(CaseTableRow, link_surrogate_id)
        if link is None:
            raise TracecatNotFoundError("Case row link was not created")

        await CaseEventsService(self.session, self.role).create_event(
            case,
            TableRowLinkedEvent(
                table_id=table.id, table_name=table.name, row_id=params.row_id
            ),
        )
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def link_rows(
        self,
        *,
        case: Case,
        table_id: uuid.UUID,
        row_ids: Sequence[uuid.UUID],
    ) -> CaseTableRowBatchLinkResponse:
        """Link a batch of existing table rows to a case."""
        requested = list(dict.fromkeys(row_ids))
        if not requested:
            return CaseTableRowBatchLinkResponse(
                linked_count=0,
                already_linked_count=0,
            )

        table = await self.tables.get_table(table_id)
        found = await self.tables.get_rows(table, requested)
        missing = [row_id for row_id in requested if row_id not in found]
        if missing:
            raise TracecatNotFoundError(
                f"{len(missing)} of {len(requested)} rows not found in table {table.name}"
            )

        await self._lock_case(case.id)

        existing_stmt = select(CaseTableRow.row_id).where(
            CaseTableRow.workspace_id == self.workspace_id,
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == table.id,
            CaseTableRow.row_id.in_(requested),
        )
        already_linked = set(
            (await self.session.execute(existing_stmt)).scalars().all()
        )
        to_link = [row_id for row_id in requested if row_id not in already_linked]
        if not to_link:
            return CaseTableRowBatchLinkResponse(
                linked_count=0,
                already_linked_count=len(requested),
            )

        table_links = await self._count_table_links(case.id, table.id)
        if table_links + len(to_link) > MAX_LINKED_ROWS_PER_TABLE:
            raise ValueError(
                f"A case can link at most {MAX_LINKED_ROWS_PER_TABLE} rows from one table"
            )

        if await self._count_links(case.id) + len(to_link) > MAX_LINKED_ROWS_PER_CASE:
            raise ValueError(
                f"A case can have at most {MAX_LINKED_ROWS_PER_CASE} linked rows"
            )

        if (
            table_links == 0
            and await self._count_distinct_tables(case.id) >= MAX_TABLES_PER_CASE
        ):
            raise ValueError(
                f"A case can link rows from at most {MAX_TABLES_PER_CASE} tables"
            )

        stmt = (
            insert(CaseTableRow)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "workspace_id": self.workspace_id,
                        "case_id": case.id,
                        "table_id": table.id,
                        "row_id": row_id,
                    }
                    for row_id in to_link
                ]
            )
            .on_conflict_do_nothing(constraint="uq_case_table_row_link")
            .returning(CaseTableRow.row_id)
        )
        inserted = list((await self.session.execute(stmt)).scalars().all())

        events = CaseEventsService(self.session, self.role)
        for row_id in inserted:
            await events.create_event(
                case,
                TableRowLinkedEvent(
                    table_id=table.id,
                    table_name=table.name,
                    row_id=row_id,
                ),
            )

        await self.session.commit()
        return CaseTableRowBatchLinkResponse(
            linked_count=len(inserted),
            already_linked_count=len(requested) - len(inserted),
        )

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

    async def unlink_rows(
        self,
        *,
        case: Case,
        table_id: uuid.UUID,
        row_ids: Sequence[uuid.UUID],
    ) -> int:
        """Unlink matching table rows from a case and skip missing links."""
        requested = list(dict.fromkeys(row_ids))
        if not requested:
            return 0

        stmt = (
            sa.delete(CaseTableRow)
            .where(
                CaseTableRow.workspace_id == self.workspace_id,
                CaseTableRow.case_id == case.id,
                CaseTableRow.table_id == table_id,
                CaseTableRow.row_id.in_(requested),
            )
            .returning(CaseTableRow.row_id)
            .execution_options(synchronize_session=False)
        )
        deleted = list((await self.session.execute(stmt)).scalars().all())
        if not deleted:
            return 0

        tables_by_id = await self._get_tables_by_id([table_id])
        table = tables_by_id.get(table_id)
        table_name = table.name if table is not None else None
        events = CaseEventsService(self.session, self.role)
        for row_id in deleted:
            await events.create_event(
                case,
                TableRowUnlinkedEvent(
                    table_id=table_id,
                    table_name=table_name,
                    row_id=row_id,
                ),
            )

        await self.session.commit()
        return len(deleted)

    async def insert_row_to_case(
        self,
        *,
        case: Case,
        params: CaseTableRowInsertCreate,
    ) -> CaseTableRow:
        table = await self.tables.get_table(params.table_id)

        # The insert and the link share one transaction. ``commit=False`` keeps
        # the row flushed but uncommitted, so the case lock taken here is still
        # held when ``link_row`` re-checks the caps and commits both writes
        # together. Reject the caps up front to avoid the pointless insert; if
        # the link is rejected anyway, or anything else fails, the flushed row
        # rolls back when the request's session closes, so no orphan row can be
        # left behind.
        await self._lock_case(case.id)
        if (message := await self._link_cap_violation(case.id, table.id)) is not None:
            raise ValueError(message)

        row = await self.tables.insert_row(table, params.row, commit=False)
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
        rows_by_table_id: dict[uuid.UUID, dict[uuid.UUID, dict[str, Any]]] = {}
        if include_row_data:
            row_ids_by_table_id: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
            for link in links:
                if link.table_id in tables_by_id:
                    row_ids_by_table_id[link.table_id].add(link.row_id)

            for table_id, row_ids in row_ids_by_table_id.items():
                try:
                    rows_by_table_id[table_id] = await self.tables.get_rows(
                        tables_by_id[table_id], list(row_ids)
                    )
                except sa_exc.DBAPIError as exc:
                    if not isinstance(exc.orig, UndefinedTableError):
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

    async def get_case_or_raise(self, case_id: uuid.UUID) -> Case:
        stmt = select(Case).where(
            Case.workspace_id == self.workspace_id,
            Case.id == case_id,
        )
        case = (await self.session.execute(stmt)).scalars().first()
        if case is None:
            raise TracecatNotFoundError(f"Case {case_id} not found")
        return case
