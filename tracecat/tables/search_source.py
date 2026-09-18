"""Bounded table source reads for chunk preparation and backfill."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from tracecat.db.models import SearchDocument, Table, TableColumn
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.search.chunking_types import ChunkingIdentity, SourceSlice
from tracecat.search.types import SearchError, SearchErrorCode
from tracecat.tables.common import sanitize_identifier
from tracecat.tables.search import TableSearchService


@dataclass(frozen=True, slots=True)
class SourceRow:
    """Source identity only; revision 1 is reserved for undiscovered rows."""

    row_id: UUID
    revision: int


@dataclass(frozen=True, slots=True)
class SourcePage:
    rows: tuple[SourceRow, ...]
    has_more: bool


class TableSearchSource(TableSearchService):
    """Caller-owned short transactions; never retain one across provider calls."""

    def physical_table(self, table: Table, *columns: TableColumn):
        """Construct SQL identifiers only from authorized database metadata."""
        return sa.table(
            sanitize_identifier(table.name),
            sa.column("id", sa.UUID),
            sa.column("__tc_workspace_id", sa.UUID),
            *[sa.column(sanitize_identifier(c.name), sa.Text) for c in columns],
            schema=f"tables_{WorkspaceUUID.new(self.scope.workspace_id).short()}",
        )

    async def scan_rows(
        self,
        collection_id: UUID,
        *,
        generation: int,
        after: UUID | None = None,
        limit: int = 100,
    ) -> SourcePage:
        """Scan identities, never text; persist discovery/checkpoint before commit.

        The worker calls touch_document(backfill=True) for these rows and advances
        checkpoint_backfill in this same transaction, while the shared lock is held.
        Normal writers capture concurrent inserts that sort behind the cursor.
        """
        if not 1 <= limit <= 1000:
            raise ValueError("Scan limit must be between 1 and 1000")
        collection = await self.collection(collection_id)
        await self._active(collection)
        if generation != collection.generation:
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        table = await self.table(collection.source_id)
        source = self.physical_table(table)
        stmt = (
            sa.select(source.c.id, sa.func.coalesce(SearchDocument.desired_revision, 1))
            .select_from(
                source.outerjoin(
                    SearchDocument,
                    sa.and_(
                        self._scope(SearchDocument),
                        SearchDocument.collection_id == collection.id,
                        SearchDocument.source_row_id == source.c.id,
                    ),
                )
            )
            .where(source.c["__tc_workspace_id"] == self.scope.workspace_id)
            .order_by(source.c.id)
            .limit(limit + 1)
        )
        if after is not None:
            stmt = stmt.where(source.c.id > after)
        rows = (await self.session.execute(stmt)).tuples().all()
        return SourcePage(
            tuple(SourceRow(row_id, revision) for row_id, revision in rows[:limit]),
            len(rows) > limit,
        )

    async def read_slice(
        self, identity: ChunkingIdentity, column_id: UUID, start: int, limit: int
    ) -> SourceSlice:
        """Read exact Unicode positions only while all supplied versions are current."""
        if start < 0 or not 1 <= limit <= 65536:
            raise ValueError("Invalid source slice bounds")
        if (
            identity.organization_id != self.scope.organization_id
            or identity.workspace_id != self.scope.workspace_id
        ):
            raise SearchError(SearchErrorCode.NOT_FOUND)
        collection = await self.collection(identity.collection_id)
        await self._active(collection)
        if (
            collection.generation != identity.generation
            or collection.config_version != identity.config_version
            or column_id not in collection.selected_column_ids
        ):
            raise SearchError(SearchErrorCode.CONFIGURATION_CHANGED)
        table = await self.table(collection.source_id)
        column = next((c for c in table.columns if c.id == column_id), None)
        if column is None:
            raise SearchError(SearchErrorCode.NOT_FOUND)
        source = self.physical_table(table, column)
        # One bounded SQL projection joins the text to the expected revision.
        # The workspace lock also excludes source DDL until this transaction ends.
        stmt = (
            sa.select(
                sa.func.coalesce(
                    sa.func.substr(
                        source.c[sanitize_identifier(column.name)], start + 1, limit + 1
                    ),
                    "",
                )
            )
            .select_from(
                source.join(SearchDocument, SearchDocument.source_row_id == source.c.id)
            )
            .where(
                self._scope(SearchDocument),
                SearchDocument.id == identity.document_id,
                SearchDocument.collection_id == collection.id,
                SearchDocument.generation == identity.generation,
                SearchDocument.desired_revision == identity.revision,
                SearchDocument.deleted_at.is_(None),
                source.c["__tc_workspace_id"] == self.scope.workspace_id,
            )
        )
        value = (await self.session.execute(stmt)).scalar_one_or_none()
        if value is None:
            raise SearchError(SearchErrorCode.STALE_CLAIM)
        return SourceSlice(text=value[:limit], end_of_column=len(value) <= limit)
