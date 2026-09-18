"""Exact row ranking and bounded original-text excerpts in one source transaction."""

import sqlalchemy as sa

from tracecat.db.models import SearchChunk, SearchCollection, SearchDocument, Table
from tracecat.query.execution import query_execution_context
from tracecat.search.cursors import RankedReference
from tracecat.search.query import eligible_chunks
from tracecat.search.schemas import SearchMatch, SearchResult
from tracecat.tables.common import sanitize_identifier
from tracecat.tables.search_source import TableSearchSource


def source_chunks(
    store: TableSearchSource,
    table: Table,
    collection: SearchCollection,
    dimensions: int,
):
    source = store.physical_table(table)
    return (
        eligible_chunks(store.scope)
        .join(source, source.c.id == SearchDocument.source_row_id)
        .where(
            SearchChunk.collection_id == collection.id,
            SearchChunk.dimensions == dimensions,
            source.c["__tc_workspace_id"] == store.scope.workspace_id,
        )
    )


async def rank_rows(
    store: TableSearchSource,
    table: Table,
    collection: SearchCollection,
    vector: tuple[float, ...],
) -> tuple[list[RankedReference], bool]:
    """Compare only materialized eligible vectors, choose each row's maximum."""
    eligible = (
        source_chunks(store, table, collection, len(vector))
        .with_only_columns(
            SearchDocument.source_row_id.label("row_id"),
            SearchChunk.id.label("chunk_id"),
            SearchChunk.revision,
            SearchChunk.column_id,
            SearchChunk.ordinal,
            SearchChunk.embedding,
        )
        .cte("eligible")
        .prefix_with("MATERIALIZED")
    )
    score = 1 - eligible.c.embedding.cosine_distance(list(vector))
    winners = sa.select(
        eligible.c.row_id,
        eligible.c.chunk_id,
        eligible.c.revision,
        score.label("score"),
        sa.func.row_number()
        .over(
            partition_by=eligible.c.row_id,
            order_by=(score.desc(), eligible.c.column_id, eligible.c.ordinal),
        )
        .label("rank"),
    ).cte("winners")
    statement = (
        sa.select(winners)
        .where(winners.c.rank == 1)
        .order_by(winners.c.score.desc(), winners.c.row_id)
        .limit(101)
    )
    async with query_execution_context(store.session, statement_timeout_ms=2000):
        rows = (await store.session.execute(statement)).mappings().all()
    return [
        RankedReference(
            row_id=row["row_id"],
            chunk_id=row["chunk_id"],
            revision=row["revision"],
            score=max(-1.0, min(1.0, row["score"])),
        )
        for row in rows[:100]
    ], len(rows) > 100


async def read_results(
    store: TableSearchSource,
    table: Table,
    collection: SearchCollection,
    dimensions: int,
    references: list[RankedReference],
) -> dict[str, SearchResult]:
    """Revalidate all references and fetch at most 1,000 characters per match."""
    if not references:
        return {}
    columns = [
        column
        for column in table.columns
        if column.id in collection.selected_column_ids
    ]
    source = store.physical_table(table, *columns)
    value = sa.case(
        *[
            (
                SearchChunk.column_id == column.id,
                source.c[sanitize_identifier(column.name)],
            )
            for column in columns
        ],
        else_=None,
    )
    end = sa.func.least(SearchChunk.end_offset, SearchChunk.start_offset + 1000)
    statement = (
        eligible_chunks(store.scope)
        .join(source, source.c.id == SearchDocument.source_row_id)
        .where(
            SearchChunk.collection_id == collection.id,
            SearchChunk.dimensions == dimensions,
            SearchChunk.id.in_([ref.chunk_id for ref in references]),
            source.c["__tc_workspace_id"] == store.scope.workspace_id,
        )
        .with_only_columns(
            SearchChunk.id,
            SearchDocument.source_row_id,
            SearchChunk.revision,
            SearchChunk.column_id,
            SearchChunk.column_name,
            SearchChunk.start_offset,
            SearchChunk.end_offset,
            sa.func.substr(
                value,
                sa.cast(SearchChunk.start_offset + 1, sa.Integer),
                sa.cast(end - SearchChunk.start_offset, sa.Integer),
            ).label("excerpt"),
        )
    )
    by_id = {str(row.id): row for row in (await store.session.execute(statement)).all()}
    results = {}
    for ref in references:
        row = by_id.get(str(ref.chunk_id))
        if (
            row is None
            or row.revision != ref.revision
            or row.source_row_id != ref.row_id
            or row.excerpt is None
        ):
            continue
        results[str(ref.chunk_id)] = SearchResult(
            row_id=ref.row_id,
            score=ref.score,
            indexed_revision=ref.revision,
            match=SearchMatch(
                column_id=row.column_id,
                column_name=row.column_name,
                text=row.excerpt,
                start=row.start_offset,
                end=row.start_offset + len(row.excerpt),
                shortened=row.end_offset - row.start_offset > len(row.excerpt),
            ),
        )
    return results
