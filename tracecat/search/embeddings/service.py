"""Automatic provider selection with short transactions and pinned embeddings."""

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.schemas import (
    EmbeddingConfigurationRead,
    EmbeddingModelRead,
)
from tracecat.search.embeddings.storage import EmbeddingSettingsStorage
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
            version, state, current = await store.current()
            pending = await store.reconciliation_pending()
        spec = selected[0].spec if selected else None
        configuration = (
            None
            if spec is None
            else EmbeddingModelRead(
                provider=spec.provider,
                model=spec.model,
                dimensions=spec.dimensions,
                tokenizer=spec.tokenizer,
                input_token_limit=spec.input_token_limit,
                input_character_limit=spec.input_character_limit,
                batch_size_limit=spec.batch_size_limit,
                batch_token_limit=spec.batch_token_limit,
            )
        )
        selected_revision = selected[0].recipe_revision if selected else None
        changed = selected_revision != (current.recipe_revision if current else None)
        return EmbeddingConfigurationRead(
            available=spec is not None,
            version=version,
            state=SearchState.DISABLED if spec is None else state,
            configuration=configuration,
            reindex_required=changed or pending,
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
        _, state, _ = await store.current()
        await store.session.commit()
    if selected is None or state != SearchState.ACTIVE:
        raise EmbeddingError(EmbeddingErrorCode.NOT_CONFIGURED)
    current, credential = selected
    if current.version != request.config_version:
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
    result = await client.embed(current, credential, request)
    async with EmbeddingSettingsStorage.with_session(scope=request.scope) as store:
        selected = await store.synchronize()
        _, state, _ = await store.current()
        await store.session.commit()
    if (
        selected is None
        or selected[0].version != request.config_version
        or state != SearchState.ACTIVE
    ):
        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
    return result
