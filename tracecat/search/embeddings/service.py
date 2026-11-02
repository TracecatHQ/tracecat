"""Automatic provider selection with short transactions and pinned embeddings."""

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.schemas import (
    EmbeddingConfigurationRead,
    EmbeddingModelRead,
)
from tracecat.search.embeddings.storage import (
    EmbeddingSettingsStorage,
    configuration_matches,
)
from tracecat.search.embeddings.types import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
)
from tracecat.search.types import EmbeddingRequest, SearchScope, SearchState


class WorkspaceEmbeddingService:
    """Expose status without a separate provider/model/secret setup flow."""

    def __init__(self, role: Role):
        if role.organization_id is None or role.workspace_id is None:
            raise TracecatAuthorizationError("Workspace context required")
        self.role = role
        self.scope = SearchScope(role.organization_id, role.workspace_id)

    @require_scope("workspace:read")
    async def get(self) -> EmbeddingConfigurationRead:
        """Read availability without writes, remote probes, or secret metadata."""
        async with EmbeddingSettingsStorage.with_session(scope=self.scope) as store:
            selected = await store.available()
            workspace, saved = await store.current()
            spec = selected[0].spec if selected else None
            changed = not configuration_matches(
                saved, selected[0] if selected else None
            )
            return EmbeddingConfigurationRead(
                available=spec is not None,
                version=workspace.current_version if workspace else 0,
                state=SearchState(workspace.state)
                if workspace and spec
                else SearchState.DISABLED,
                configuration=(
                    EmbeddingModelRead.model_validate(spec, from_attributes=True)
                    if spec
                    else None
                ),
                reindex_required=changed
                or bool(workspace and workspace.reconciliation_required),
            )


async def resolve_embedding_configuration(
    scope: SearchScope,
) -> PinnedConfiguration | None:
    """Reconcile provider settings before indexing/query preparation.

    Call only after source/query authorization, outside the source-write path.
    None means skip embedding work. No network call is needed for selection.
    """
    async with EmbeddingSettingsStorage.with_session(scope=scope) as store:
        selected = await store.synchronize()
        await store.session.commit()
        return selected[0] if selected else None


async def embed_current(
    request: EmbeddingRequest, client: EmbeddingClient
) -> EmbeddingBatch:
    """Use the pinned model and reject changed settings after the provider call.

    Provider failures propagate as typed errors. Never retry against another model.
    Publication/ranking must also check the version in their own transaction.
    """
    async with EmbeddingSettingsStorage.with_session(scope=request.scope) as store:
        selected = await store.synchronize()
        workspace, _ = await store.current()
        active = workspace is not None and workspace.state == SearchState.ACTIVE
        await store.session.commit()
    if selected is None or not active:
        raise EmbeddingError(EmbeddingErrorCode.NOT_CONFIGURED)
    current, credential = selected
    if current.version != request.config_version:
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
    result = await client.embed(current, credential, request)
    async with EmbeddingSettingsStorage.with_session(scope=request.scope) as store:
        selected = await store.synchronize()
        workspace, _ = await store.current()
        active = workspace is not None and workspace.state == SearchState.ACTIVE
        await store.session.commit()
    if selected is None or selected[0].version != request.config_version or not active:
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
    return result
