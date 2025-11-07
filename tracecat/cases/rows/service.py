"""Service for managing case table rows."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import CaseTableRowLink
from tracecat.db.models import Case, CaseTableRow, Table
from tracecat.exceptions import (
    TracecatNotFoundError,
)
from tracecat.logger import logger
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TablesService


class CaseTableRowService(BaseWorkspaceService):
    """Service for managing case table rows."""

    service_name = "case_table_rows"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.tables_service = TablesService(session, role)

    async def list_case_table_rows(
        self, case: Case, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[dict[str, Any]]:
        """List paginated table rows for a case.

        Args:
            case: The case to list table rows for
            params: Pagination parameters

        Returns:
            Paginated response with case table row links and row data
        """
        paginator = BaseCursorPaginator(self.session)

        stmt = (
            select(CaseTableRow)
            .where(CaseTableRow.case_id == case.id)
            .options(
                selectinload(CaseTableRow.table),  # type: ignore
            )
            .order_by(col(CaseTableRow.created_at).desc(), col(CaseTableRow.id).desc())
        )

        # Apply cursor pagination
        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_time = cursor_data.created_at
            cursor_id = uuid.UUID(cursor_data.id)

            if params.reverse:
                stmt = stmt.where(
                    or_(
                        col(CaseTableRow.created_at) > cursor_time,
                        and_(
                            col(CaseTableRow.created_at) == cursor_time,
                            col(CaseTableRow.id) > cursor_id,
                        ),
                    )
                ).order_by(
                    col(CaseTableRow.created_at).asc(), col(CaseTableRow.id).asc()
                )
            else:
                stmt = stmt.where(
                    or_(
                        col(CaseTableRow.created_at) < cursor_time,
                        and_(
                            col(CaseTableRow.created_at) == cursor_time,
                            col(CaseTableRow.id) < cursor_id,
                        ),
                    )
                )

        stmt = stmt.limit(params.limit + 1)
        result = await self.session.exec(stmt)
        all_items = result.all()

        has_more = len(all_items) > params.limit
        page_items = all_items[: params.limit] if has_more else all_items

        if params.reverse and params.cursor:
            page_items = list(page_items)
            page_items.reverse()

        # Group links by table to batch load rows
        links_by_table: dict[uuid.UUID, list[CaseTableRow]] = {}
        table_cache: dict[uuid.UUID, Table] = {}
        for link in page_items:
            if link.table is not None:
                table_cache[link.table_id] = link.table
            links_by_table.setdefault(link.table_id, []).append(link)

        rows_with_data: list[dict[str, Any]] = []
        for table_id, links in links_by_table.items():
            table = table_cache.get(table_id) or await self.tables_service.get_table(
                table_id
            )
            table_cache[table_id] = table

            try:
                fetched_rows_by_id = await self.tables_service.get_rows_by_ids(
                    table, [link.row_id for link in links]
                )
            except Exception as e:
                logger.warning(
                    "Failed to batch fetch row data for table",
                    table_id=table_id,
                    error=str(e),
                )
                fetched_rows_by_id = {}

            for link in links:
                row_data = fetched_rows_by_id.get(link.row_id)
                if row_data is None:
                    logger.warning(
                        "Linked table row missing during fetch",
                        table_id=table_id,
                        row_id=link.row_id,
                        link_id=link.id,
                    )
                    row_data = {}

                rows_with_data.append(
                    {
                        "id": link.id,
                        "case_id": link.case_id,
                        "table_id": link.table_id,
                        "row_id": link.row_id,
                        "table_name": table.name,
                        "row_data": row_data,
                        "created_at": link.created_at,
                        "updated_at": link.updated_at,
                    }
                )

        # Build pagination response
        next_cursor = None
        prev_cursor = None
        has_previous = params.cursor is not None

        if has_more and page_items:
            last_item = page_items[-1]
            next_cursor = paginator.encode_cursor(
                last_item.created_at, str(last_item.id)
            )

        if params.cursor and page_items:
            first_item = page_items[0]
            prev_cursor = paginator.encode_cursor(
                first_item.created_at, str(first_item.id)
            )

        return CursorPaginatedResponse(
            items=rows_with_data,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=None,  # Could add table stats if needed
        )

    async def get_case_table_row(
        self, case: Case, link_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Get a specific case table row link by ID.

        Args:
            case: The case the row link belongs to
            link_id: The case table row link ID

        Returns:
            The case table row link with row data if found, None otherwise
        """
        stmt = (
            select(CaseTableRow)
            .where(
                CaseTableRow.case_id == case.id,
                CaseTableRow.id == link_id,
            )
            .options(
                selectinload(CaseTableRow.table),  # type: ignore
            )
        )
        result = await self.session.exec(stmt)
        link = result.first()

        if link is None:
            return None

        # Get table metadata
        table = link.table
        if table is None:
            table = await self.tables_service.get_table(link.table_id)

        # Get row data from dynamic table
        try:
            row_data = await self.tables_service.get_row(table, link.row_id)
        except Exception as e:
            logger.warning(
                "Failed to fetch row data",
                table_id=link.table_id,
                row_id=link.row_id,
                error=str(e),
            )
            row_data = {}

        return {
            "id": link.id,
            "case_id": link.case_id,
            "table_id": link.table_id,
            "row_id": link.row_id,
            "table_name": table.name,
            "row_data": row_data,
            "created_at": link.created_at,
            "updated_at": link.updated_at,
        }

    async def link_table_row(
        self, case: Case, params: CaseTableRowLink
    ) -> CaseTableRow:
        """Link an existing table row to a case.

        This operation is idempotent: if the row is already linked, returns
        the existing link instead of raising an error.

        Args:
            case: The case to link the row to
            params: Parameters containing table_id and row_id

        Returns:
            The case table row link (created or existing)

        Raises:
            TracecatNotFoundError: If table or row doesn't exist
        """
        # Verify the table exists and belongs to this workspace
        # get_table() already verifies workspace ownership
        table = await self.tables_service.get_table(params.table_id)

        # Verify the row exists in the table
        try:
            await self.tables_service.get_row(table, params.row_id)
        except TracecatNotFoundError as e:
            raise TracecatNotFoundError(
                f"Row {params.row_id} not found in table {table.name}"
            ) from e

        # Check if already linked (idempotent: return existing if present)
        existing_stmt = select(CaseTableRow).where(
            CaseTableRow.case_id == case.id,
            CaseTableRow.table_id == params.table_id,
            CaseTableRow.row_id == params.row_id,
        )
        existing = await self.session.exec(existing_stmt)
        existing_link = existing.first()
        if existing_link:
            logger.info(
                "Table row already linked to case, returning existing link",
                case_id=case.id,
                table_id=params.table_id,
                row_id=params.row_id,
                link_id=existing_link.id,
            )
            return existing_link

        # Create the link
        case_table_row = CaseTableRow(
            owner_id=self.workspace_id,
            case_id=case.id,
            table_id=params.table_id,
            row_id=params.row_id,
        )
        self.session.add(case_table_row)
        await self.session.commit()
        await self.session.refresh(case_table_row)

        logger.info(
            "Linked table row to case",
            case_id=case.id,
            table_id=params.table_id,
            row_id=params.row_id,
            link_id=case_table_row.id,
        )

        return case_table_row

    async def unlink_table_row(self, case_table_row: CaseTableRow) -> None:
        """Unlink a table row from a case (soft delete - removes link only).

        Args:
            case_table_row: The case table row link to remove
        """
        if case_table_row.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Case table row not found")

        await self.session.delete(case_table_row)
        await self.session.commit()

        logger.info(
            "Unlinked table row from case",
            link_id=case_table_row.id,
            case_id=case_table_row.case_id,
            table_id=case_table_row.table_id,
            row_id=case_table_row.row_id,
        )
