"""Eligibility shared by ranking and background consumers.

The table search adapter must additionally join the physical source row. A
cached cursor must reapply that join and this predicate on every page.
"""

from sqlalchemy import select

from tracecat.db.models import (
    SearchChunk,
    SearchCollection,
    SearchDocument,
    SearchWorkspaceState,
    Table,
    Workspace,
)
from tracecat.search.types import SearchScope


def eligible_chunks(scope: SearchScope):
    """Select chunks only from fully published current documents and configs."""
    return (
        select(SearchChunk)
        .join(SearchDocument, SearchDocument.id == SearchChunk.document_id)
        .join(SearchCollection, SearchCollection.id == SearchDocument.collection_id)
        .join(
            SearchWorkspaceState,
            (SearchWorkspaceState.workspace_id == SearchChunk.workspace_id)
            & (SearchWorkspaceState.organization_id == SearchChunk.organization_id),
        )
        .join(
            Workspace,
            (Workspace.id == SearchChunk.workspace_id)
            & (Workspace.organization_id == SearchChunk.organization_id),
        )
        .join(
            Table,
            (Table.id == SearchCollection.source_id)
            & (Table.workspace_id == SearchChunk.workspace_id),
        )
        .where(
            SearchChunk.workspace_id == scope.workspace_id,
            SearchChunk.organization_id == scope.organization_id,
            SearchWorkspaceState.state == "active",
            SearchCollection.enabled.is_(True),
            SearchCollection.deleted_at.is_(None),
            SearchCollection.config_version == SearchWorkspaceState.current_version,
            SearchChunk.config_version == SearchCollection.config_version,
            SearchDocument.deleted_at.is_(None),
            SearchDocument.state == "ready",
            SearchDocument.indexed_revision == SearchDocument.desired_revision,
            SearchChunk.revision == SearchDocument.indexed_revision,
            SearchChunk.generation == SearchDocument.generation,
            SearchDocument.generation == SearchCollection.generation,
            SearchChunk.state == "embedded",
        )
    )
