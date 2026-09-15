"""Short scoped configuration transactions; never call a provider here."""

import hashlib
import uuid

from pydantic import SecretStr
from sqlalchemy import select

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.models import SearchWorkspaceState, Secret, Workspace
from tracecat.search.embeddings.catalog import get_model
from tracecat.search.embeddings.schemas import EmbeddingConfigurationInput
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.service import SearchStorage
from tracecat.search.types import SearchState
from tracecat.secrets.encryption import decrypt_keyvalues


class EmbeddingSettingsStorage(SearchStorage):
    """Reuse the search workspace lock, tenant scope and owned-session lifecycle."""

    async def current(self) -> tuple[int, SearchState, PinnedConfiguration | None]:
        """Read a detached configuration without creating state for ordinary reads."""
        from_state = await self.session.scalar(
            select(SearchWorkspaceState)
            .join(Workspace, Workspace.id == SearchWorkspaceState.workspace_id)
            .where(
                self._scope(SearchWorkspaceState),
                Workspace.organization_id == self.scope.organization_id,
            )
        )
        if from_state is None:
            return 0, SearchState.DISABLED, None
        config = await self._configuration(from_state.current_version)
        if config is None:
            return from_state.current_version, SearchState(from_state.state), None
        spec = get_model(config.provider, config.model)
        if (
            config.dimensions != spec.dimensions
            or config.input_token_limit != spec.input_token_limit
            or config.endpoint != spec.endpoint
        ):
            raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
        return (
            config.version,
            SearchState(from_state.state),
            PinnedConfiguration(
                config.version,
                spec,
                config.credential_id,
                config.credential_environment,
            ),
        )

    async def credential(
        self, credential_id: uuid.UUID, environment: str
    ) -> ResolvedCredential:
        """Resolve only a live workspace's exact secret binding, holding it for this transaction."""
        secret = await self.session.scalar(
            select(Secret)
            .join(Workspace, Workspace.id == Secret.workspace_id)
            .where(
                Secret.id == credential_id,
                Secret.workspace_id == self.scope.workspace_id,
                Workspace.organization_id == self.scope.organization_id,
                Secret.environment == environment,
                Secret.type == "custom",
            )
            .with_for_update(read=True, of=Secret)
        )
        if secret is None:
            raise EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
        try:
            keys = decrypt_keyvalues(secret.encrypted_keys, key=get_db_encryption_key())
            values = [item.value for item in keys if item.key == "OPENAI_API_KEY"]
            if len(values) != 1 or not values[0].get_secret_value().strip():
                raise ValueError("Invalid credential")
            return ResolvedCredential(
                SecretStr(values[0].get_secret_value()),
                hashlib.sha256(secret.encrypted_keys).digest(),
            )
        except Exception:
            # Discard decryption/validation exceptions completely, including chains.
            error = EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
        raise error

    async def save_validated(
        self,
        params: EmbeddingConfigurationInput,
        previous: PinnedConfiguration | None,
        expected_version: int,
        credential: ResolvedCredential,
    ) -> None:
        """Recheck validation's snapshot and atomically save semantics or rotate access."""
        await self.lock_scope()
        version, _, current = await self.current()
        if version != expected_version or current != previous:
            raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
        latest = await self.credential(
            params.credential_id, params.credential_environment
        )
        if latest.fingerprint != credential.fingerprint:
            raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
        spec = get_model(params.provider, params.model)
        if current is not None and current.spec == spec:
            # Only access credentials are mutable; the vector coordinate system is not.
            record = await self._configuration(version)
            assert record is not None
            record.credential_id = params.credential_id
            record.credential_environment = params.credential_environment
        else:
            await self.save_configuration(
                provider=spec.provider,
                model=spec.model,
                endpoint=spec.endpoint,
                credential_id=params.credential_id,
                credential_environment=params.credential_environment,
                dimensions=spec.dimensions,
                input_token_limit=spec.input_token_limit,
            )
        await self.set_state(SearchState.ACTIVE)

    async def disable(self, expected_version: int) -> None:
        """Advance the pointer so stale calls and a later re-save cannot revive old work."""
        await self.lock_scope()
        state = await self._state()
        if state.current_version != expected_version:
            raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_CHANGED)
        await self.set_state(SearchState.REINDEX_REQUIRED)
        await self.set_state(SearchState.DISABLED)
