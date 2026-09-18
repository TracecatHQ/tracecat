"""Resolve existing provider settings and pin embedding semantics per workspace."""

from dataclasses import replace

from sqlalchemy import exists, or_, select

from tracecat.agent.default_model import resolve_org_default_model
from tracecat.db.models import (
    AgentCatalog,
    AgentModelAccess,
    OrganizationSecret,
    SearchEmbeddingConfig,
    SearchWorkspaceState,
    Workspace,
)
from tracecat.search.embeddings.catalog import PROVIDER_ORDER
from tracecat.search.embeddings.selection import select_configuration
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ProviderConnection,
    ResolvedCredential,
)
from tracecat.search.service import SearchStorage
from tracecat.search.types import SearchState
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT


def configuration_matches(
    saved: SearchEmbeddingConfig | None, selected: PinnedConfiguration | None
) -> bool:
    """Compare saved semantics directly; credential rotation is intentionally ignored."""
    if saved is None or selected is None:
        return saved is None and selected is None
    spec = selected.spec
    return (
        saved.recipe_revision == selected.recipe_revision
        and saved.provider == spec.provider
        and saved.model == spec.model
        and (saved.endpoint or "") == spec.endpoint
        and saved.dimensions == spec.dimensions
        and saved.input_token_limit == spec.input_token_limit
    )


class EmbeddingSettingsStorage(SearchStorage):
    """Internal boundary for trusted indexing/query scopes, never caller-picked secrets."""

    async def current(
        self,
    ) -> tuple[SearchWorkspaceState | None, SearchEmbeddingConfig | None]:
        """Read the current workspace state and its saved configuration together."""
        state = await self.session.scalar(
            select(SearchWorkspaceState).where(self._scope(SearchWorkspaceState))
        )
        saved = await self._configuration(state.current_version) if state else None
        return state, saved

    async def available(self) -> tuple[PinnedConfiguration, ResolvedCredential] | None:
        """Load permitted connections and select a recipe without provider calls."""
        connections = await self._load_connections()
        if not connections:
            return None
        default = await resolve_org_default_model(
            self.session, self.scope.organization_id
        )
        return select_configuration(
            connections,
            default.model_provider if default else None,
        )

    async def _load_connections(self) -> list[ProviderConnection]:
        """Load effective workspace model access and lock candidate secrets together."""
        live = await self.session.scalar(
            select(Workspace.id).where(
                Workspace.id == self.scope.workspace_id,
                Workspace.organization_id == self.scope.organization_id,
            )
        )
        if live is None:
            raise EmbeddingError(EmbeddingErrorCode.NOT_CONFIGURED)
        override = await self.session.scalar(
            select(
                exists().where(
                    AgentModelAccess.organization_id == self.scope.organization_id,
                    AgentModelAccess.workspace_id == self.scope.workspace_id,
                )
            )
        )
        allowed = (
            (
                await self.session.execute(
                    select(AgentCatalog.model_provider, AgentCatalog.model_name)
                    .join(
                        AgentModelAccess, AgentModelAccess.catalog_id == AgentCatalog.id
                    )
                    .where(
                        AgentModelAccess.organization_id == self.scope.organization_id,
                        AgentModelAccess.workspace_id == self.scope.workspace_id
                        if override
                        else AgentModelAccess.workspace_id.is_(None),
                        or_(
                            AgentCatalog.organization_id == self.scope.organization_id,
                            AgentCatalog.organization_id.is_(None),
                        ),
                        AgentCatalog.custom_provider_id.is_(None),
                    )
                    .distinct()
                )
            )
            .tuples()
            .all()
        )
        models = {
            provider: frozenset(name for source, name in allowed if source == provider)
            for provider in PROVIDER_ORDER
        }
        secrets = await self.session.scalars(
            select(OrganizationSecret)
            .where(
                OrganizationSecret.organization_id == self.scope.organization_id,
                OrganizationSecret.name.in_(
                    [f"agent-{p}-credentials" for p in models if models[p]]
                ),
                OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
                OrganizationSecret.type == "custom",
            )
            .order_by(OrganizationSecret.id)
            .with_for_update(read=True)
        )
        by_name = {secret.name: secret for secret in secrets}
        return [
            ProviderConnection(
                provider,
                models[provider],
                secret.id,
                secret.environment,
                secret.encrypted_keys,
            )
            for provider in PROVIDER_ORDER
            if (secret := by_name.get(f"agent-{provider}-credentials")) is not None
        ]

    async def synchronize(
        self,
    ) -> tuple[PinnedConfiguration, ResolvedCredential] | None:
        """Pin automatic selection before work; callers commit before provider I/O."""
        await self.lock_scope()
        selected = await self.available()
        workspace, saved = await self.current()
        paused = workspace is not None and workspace.state == SearchState.PAUSED
        if selected is None:
            if saved is not None:
                await self.set_state(SearchState.REINDEX_REQUIRED)
                await self.set_state(
                    SearchState.PAUSED if paused else SearchState.DISABLED
                )
            return None
        candidate, credential = selected
        if not configuration_matches(saved, candidate):
            saved = await self.save_configuration(
                provider=candidate.spec.provider,
                model=candidate.spec.model,
                endpoint=candidate.spec.endpoint,
                credential_id=candidate.credential_id,
                credential_environment=candidate.credential_environment,
                dimensions=candidate.spec.dimensions,
                input_token_limit=candidate.spec.input_token_limit,
                recipe_revision=candidate.recipe_revision,
            )
        else:
            assert saved is not None
            saved.credential_id = candidate.credential_id
            saved.credential_environment = candidate.credential_environment
        if not paused:
            await self.set_state(SearchState.ACTIVE)
        return replace(candidate, version=saved.version), credential
