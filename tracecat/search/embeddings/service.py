"""Coordinate database snapshots and network calls without overlapping their lifetimes."""

import hashlib
from dataclasses import asdict

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.search.embeddings.catalog import MODELS, get_model
from tracecat.search.embeddings.client import EmbeddingClient
from tracecat.search.embeddings.schemas import (
    EmbeddingConfigurationInput,
    EmbeddingConfigurationRead,
    EmbeddingConfigurationSave,
    EmbeddingModelRead,
    EmbeddingValidationRead,
)
from tracecat.search.embeddings.storage import EmbeddingSettingsStorage
from tracecat.search.embeddings.types import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.types import (
    EmbeddingInput,
    EmbeddingRequest,
    SearchScope,
    SearchState,
)


class WorkspaceEmbeddingService:
    """Session-free coordinator for an already authenticated workspace actor.

    Storage owns each short session. Keeping this coordinator session-free makes
    it impossible to accidentally retain a caller's transaction during a probe.
    """

    def __init__(self, role: Role, client: EmbeddingClient):
        if role.organization_id is None or role.workspace_id is None:
            raise TracecatAuthorizationError("Workspace context required")
        self.role = role
        self.scope = SearchScope(role.organization_id, role.workspace_id)
        self.client = client

    @require_scope("workspace:read", "secret:read")
    async def get(self) -> EmbeddingConfigurationRead:
        """Get current settings and the finite supported catalog in one response."""
        async with EmbeddingSettingsStorage.with_session(scope=self.scope) as store:
            version, state, current = await store.current()
        configuration = None
        if current is not None:
            configuration = EmbeddingConfigurationInput(
                provider=current.spec.provider,
                model=current.spec.model,
                credential_id=current.credential_id,
                credential_environment=current.credential_environment,
            )
        return EmbeddingConfigurationRead(
            version=version,
            state=state,
            configuration=configuration,
            supported_models=tuple(
                EmbeddingModelRead(**asdict(spec)) for spec in MODELS
            ),
        )

    async def _probe(
        self,
        params: EmbeddingConfigurationInput,
    ) -> tuple[EmbeddingBatch, ResolvedCredential]:
        spec = get_model(params.provider, params.model)
        async with EmbeddingSettingsStorage.with_session(scope=self.scope) as store:
            credential = await store.credential(
                params.credential_id, params.credential_environment
            )
        # Only a constant synthetic sentence leaves the workspace during setup.
        text = "Tracecat semantic search configuration check."
        request = EmbeddingRequest(
            self.scope,
            0,
            spec.dimensions,
            (EmbeddingInput(0, hashlib.sha256(text.encode()).hexdigest(), text),),
        )
        pinned = PinnedConfiguration(
            0, spec, params.credential_id, params.credential_environment
        )
        result = await self.client.embed(pinned, credential, request)
        return result, credential

    @require_scope("workspace:update", "secret:read")
    async def validate(
        self, params: EmbeddingConfigurationInput
    ) -> EmbeddingValidationRead:
        """Test a proposal using synthetic text, leaving saved settings untouched."""
        result, _ = await self._probe(params)
        return EmbeddingValidationRead(
            dimensions=get_model(params.provider, params.model).dimensions,
            prompt_tokens=result.prompt_tokens,
        )

    @require_scope("workspace:update", "secret:read")
    async def save(
        self, params: EmbeddingConfigurationSave
    ) -> EmbeddingConfigurationRead:
        """Validate first; then reject stale snapshots or commit the new configuration."""
        async with EmbeddingSettingsStorage.with_session(scope=self.scope) as store:
            version, _, previous = await store.current()
            if version != params.expected_version:
                raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
        _, credential = await self._probe(params)
        async with EmbeddingSettingsStorage.with_session(scope=self.scope) as store:
            await store.save_validated(
                params, previous, params.expected_version, credential
            )
            await store.session.commit()
        return await self.get()

    @require_scope("workspace:update", "secret:read")
    async def disable(self, expected_version: int) -> EmbeddingConfigurationRead:
        """Immediately make the old configuration unavailable to all consumers."""
        async with EmbeddingSettingsStorage.with_session(scope=self.scope) as store:
            await store.disable(expected_version)
            await store.session.commit()
        return await self.get()


async def embed_current(
    request: EmbeddingRequest, client: EmbeddingClient
) -> EmbeddingBatch:
    """Embed for a trusted worker/query scope, rechecking state after the network call.

    Upstream must authorize the source/query before invoking this internal helper.
    The configured secret is delegated to search, independent of workflow secret
    environments or the caller's right to read the underlying credential.
    Publication/ranking must repeat the version check in their own transaction.
    """
    async with EmbeddingSettingsStorage.with_session(scope=request.scope) as store:
        version, state, current = await store.current()
        if state != SearchState.ACTIVE or current is None:
            raise EmbeddingError(EmbeddingErrorCode.NOT_CONFIGURED)
        if version != request.config_version:
            raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
        credential = await store.credential(
            current.credential_id, current.credential_environment
        )
    result = await client.embed(current, credential, request)
    async with EmbeddingSettingsStorage.with_session(scope=request.scope) as store:
        version, state, _ = await store.current()
        if version != request.config_version or state != SearchState.ACTIVE:
            raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
    return result
