"""Bounded orphan cleanup for a trusted maintenance session with RLS bypass.

This module is not an API and must not receive caller-controlled tenant bypass.
Source deletion leaves metadata and chunks; no source API waits for this work.
"""

from sqlalchemy import delete, exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    SearchChunk,
    SearchCollection,
    SearchDocument,
    SearchEmbeddingConfig,
    SearchWorkspaceState,
    Table,
    Workspace,
)


async def cleanup_orphans(session: AsyncSession, *, limit: int = 1000) -> int:
    """Remove at most limit rows per derived table, children before parents."""
    if not 1 <= limit <= 1000:
        raise ValueError("cleanup limit must be between 1 and 1000")
    removed = 0
    for model in (SearchChunk, SearchDocument, SearchCollection):
        source_id = (
            model.source_id if model is SearchCollection else SearchCollection.source_id
        )
        live_workspace = exists(
            select(Workspace.id).where(
                Workspace.id == model.workspace_id,
                Workspace.organization_id == model.organization_id,
            )
        )
        live_source = exists(
            select(Table.id).where(
                Table.id == source_id, Table.workspace_id == model.workspace_id
            )
        )
        if model is SearchCollection:
            candidates = select(model.id).where(~live_workspace | ~live_source)
        else:
            candidates = (
                select(model.id)
                .join(
                    SearchCollection,
                    SearchCollection.id
                    == (
                        SearchDocument.collection_id
                        if model is SearchDocument
                        else SearchChunk.collection_id
                    ),
                )
                .where(~live_workspace | ~live_source)
            )
        if model is SearchDocument:
            candidates = candidates.where(
                ~exists(
                    select(SearchChunk.id).where(SearchChunk.document_id == model.id)
                )
            )
        if model is SearchCollection:
            candidates = candidates.where(
                ~exists(
                    select(SearchDocument.id).where(
                        SearchDocument.collection_id == model.id
                    )
                )
            )
        ids = candidates.order_by(model.id).limit(limit)
        deleted = (
            await session.scalars(
                delete(model).where(model.id.in_(ids)).returning(model.id)
            )
        ).all()
        removed += len(deleted)
    # Configs and workspace state have composite primary keys. Bound each
    # deletion directly, including workspaces with a long configuration history.
    configs = (
        select(
            SearchEmbeddingConfig.organization_id,
            SearchEmbeddingConfig.workspace_id,
            SearchEmbeddingConfig.version,
        )
        .where(
            ~exists(
                select(Workspace.id).where(
                    Workspace.id == SearchEmbeddingConfig.workspace_id
                )
            ),
            ~exists(
                select(SearchCollection.id).where(
                    SearchCollection.workspace_id == SearchEmbeddingConfig.workspace_id,
                    SearchCollection.config_version == SearchEmbeddingConfig.version,
                )
            ),
            ~exists(
                select(SearchChunk.id).where(
                    SearchChunk.workspace_id == SearchEmbeddingConfig.workspace_id,
                    SearchChunk.config_version == SearchEmbeddingConfig.version,
                )
            ),
        )
        .limit(limit)
    )
    removed += len(
        (
            await session.scalars(
                delete(SearchEmbeddingConfig)
                .where(
                    tuple_(
                        SearchEmbeddingConfig.organization_id,
                        SearchEmbeddingConfig.workspace_id,
                        SearchEmbeddingConfig.version,
                    ).in_(configs)
                )
                .returning(SearchEmbeddingConfig.version)
            )
        ).all()
    )
    states = (
        select(SearchWorkspaceState.organization_id, SearchWorkspaceState.workspace_id)
        .where(
            ~exists(
                select(Workspace.id).where(
                    Workspace.id == SearchWorkspaceState.workspace_id
                )
            ),
        )
        .limit(limit)
    )
    removed += len(
        (
            await session.scalars(
                delete(SearchWorkspaceState)
                .where(
                    tuple_(
                        SearchWorkspaceState.organization_id,
                        SearchWorkspaceState.workspace_id,
                    ).in_(states)
                )
                .returning(SearchWorkspaceState.workspace_id)
            )
        ).all()
    )
    return removed
