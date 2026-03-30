"""Runtime resolution for agent execution paths.

This service owns the last-mile execution context: which enabled catalog row
matches a request, which credentials should be surfaced to the runtime, and
which config overrides should survive that resolution step.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator

from sqlalchemy import select
from tracecat_registry._internal import secrets as registry_secrets

from tracecat.agent.legacy_model_matching import (
    resolve_enabled_catalog_match_for_provider_model,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.provider_config import (
    credential_secret_name,
    deserialize_secret_keyvalues,
    deserialize_source_config,
    map_source_credentials,
    source_runtime_base_url,
)
from tracecat.agent.runtime.types import ResolvedExecutionContext
from tracecat.agent.schemas import ModelSelection
from tracecat.agent.selections.service import AgentSelectionsService
from tracecat.agent.types import AgentConfig, ModelSourceType
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentCatalog,
    AgentModelSelectionLink,
    AgentSource,
    OrganizationSecret,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.secrets import secrets_manager
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.service import BaseOrgService


class AgentRuntimeService(BaseOrgService):
    """Runtime model resolution, credential assembly, and config context managers."""

    service_name = "agent-runtime"

    def __init__(self, session, role=None):
        super().__init__(session, role=role)
        self.selections = AgentSelectionsService(session, role=role)
        try:
            self.presets = AgentPresetService(session=self.session, role=self.role)
        except TracecatAuthorizationError:
            self.presets = None

    async def _load_provider_credentials(self, provider: str) -> dict[str, str] | None:
        secret_stmt = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.name == credential_secret_name(provider),
            OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
        )
        secret = (await self.session.execute(secret_stmt)).scalar_one_or_none()
        if secret is None:
            return None
        return deserialize_secret_keyvalues(secret.encrypted_keys)

    async def _load_source(self, source_id: uuid.UUID | None) -> AgentSource | None:
        if source_id is None:
            return None
        stmt = select(AgentSource).where(AgentSource.id == source_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _build_runtime_credentials(
        self,
        *,
        catalog: AgentCatalog | None,
        source: AgentSource | None,
        selection_link: AgentModelSelectionLink | None,
        config: AgentConfig,
    ) -> dict[str, str]:
        # Direct-provider requests only need the org secret for that provider.
        if catalog is None:
            if config.model_provider:
                return (
                    await self._load_provider_credentials(config.model_provider) or {}
                )
            return {}
        # Catalog-backed direct providers still read from the provider secret
        # store, with per-selection overrides layered on top when present.
        if source is None:
            credentials = (
                await self._load_provider_credentials(catalog.model_provider) or {}
            )
            if (
                selection_link is not None
                and catalog.model_provider == ModelSourceType.BEDROCK.value
                and selection_link.enabled_config
            ):
                if inference_profile_id := selection_link.enabled_config.get(
                    "bedrock_inference_profile_id"
                ):
                    credentials["AWS_INFERENCE_PROFILE_ID"] = inference_profile_id
            return credentials

        # Source-backed models carry runtime auth in the source config, then map
        # that config into the env vars the downstream runtime expects.
        source_config = deserialize_source_config(source.encrypted_config)
        runtime_base_url = source_runtime_base_url(source, source_config=source_config)
        return map_source_credentials(
            source=source,
            source_config=source_config,
            model_provider=catalog.model_provider,
            runtime_base_url=runtime_base_url,
        )

    def _runtime_config_from_catalog(
        self,
        *,
        catalog: AgentCatalog,
        source: AgentSource | None,
        overrides: AgentConfig,
    ) -> AgentConfig:
        base_url = None
        if source is not None:
            base_url = source_runtime_base_url(source)
        if overrides.base_url:
            base_url = overrides.base_url
        return AgentConfig(
            source_id=catalog.source_id,
            model_name=catalog.model_name,
            model_provider=catalog.model_provider,
            base_url=base_url,
            instructions=overrides.instructions,
            output_type=overrides.output_type,
            actions=overrides.actions,
            namespaces=overrides.namespaces,
            tool_approvals=overrides.tool_approvals,
            model_settings=overrides.model_settings,
            mcp_servers=overrides.mcp_servers,
            retries=overrides.retries,
            deps_type=overrides.deps_type,
            custom_tools=overrides.custom_tools,
            enable_internet_access=overrides.enable_internet_access,
        )

    async def _resolve_enabled_context(
        self,
        selection: ModelSelection,
        *,
        workspace_id: uuid.UUID | None,
        overrides: AgentConfig,
    ) -> ResolvedExecutionContext:
        # Selection lookup is the boundary between the selections domain and the
        # runtime domain. Everything after this point should operate on the
        # resolved catalog row instead of the caller's raw model tuple.
        catalog, selection_link = await self.selections._resolve_enabled_catalog(
            self.selections._lookup_from_selection(selection),
            workspace_id=workspace_id,
        )
        source = await self._load_source(catalog.source_id)
        config = self._runtime_config_from_catalog(
            catalog=catalog,
            source=source,
            overrides=overrides,
        )
        credentials = await self._build_runtime_credentials(
            catalog=catalog,
            source=source,
            selection_link=selection_link,
            config=config,
        )
        return ResolvedExecutionContext(
            config=config,
            credentials=credentials,
            catalog=catalog,
            selection_link=selection_link,
            source=source,
        )

    async def _resolve_preset_selection(
        self,
        config: AgentConfig,
    ) -> ModelSelection | None:
        # Presets can still point at a raw provider/model pair, so normalize
        # that into a concrete enabled selection before falling back to legacy
        # model-name matching.
        if config.source_id is not None or (
            config.model_provider and config.model_name
        ):
            selection = ModelSelection(
                source_id=config.source_id,
                model_provider=config.model_provider,
                model_name=config.model_name,
            )
            if await self.selections.is_model_enabled(
                self.selections._lookup_from_selection(selection),
                workspace_id=self.role.workspace_id,
            ):
                return selection
        # When the caller explicitly supplied a source_id, do not fall back to
        # a source-agnostic match — that could silently route to a different
        # source's credentials for the same provider/model pair.
        if config.source_id is not None:
            return None
        if not config.model_provider or not config.model_name:
            return None
        match_result = await resolve_enabled_catalog_match_for_provider_model(
            self.session,
            organization_id=self.organization_id,
            workspace_id=self.role.workspace_id,
            model_provider=config.model_provider,
            model_name=config.model_name,
        )
        return self.selections._selection_from_match(match_result)

    async def resolve_execution_context(
        self,
        config: AgentConfig,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> ResolvedExecutionContext:
        effective_workspace_id = workspace_id or self.role.workspace_id
        if config.model_name and config.model_provider:
            selection = ModelSelection(
                source_id=config.source_id,
                model_provider=config.model_provider,
                model_name=config.model_name,
            )
            try:
                return await self._resolve_enabled_context(
                    selection,
                    workspace_id=effective_workspace_id,
                    overrides=config,
                )
            except TracecatNotFoundError:
                pass
        if preset_selection := await self._resolve_preset_selection(config):
            return await self._resolve_enabled_context(
                preset_selection,
                workspace_id=effective_workspace_id,
                overrides=config,
            )
        # When the caller pinned a source_id, every resolution path above has
        # already failed.  Falling through to the bare-provider credential
        # lookup would silently use different credentials than the caller
        # intended, so fail fast instead.
        if config.source_id is not None:
            raise TracecatNotFoundError(
                f"No enabled catalog entry found for source_id={config.source_id}, "
                f"model_provider={config.model_provider!r}, "
                f"model_name={config.model_name!r}"
            )
        credentials = await self._build_runtime_credentials(
            catalog=None,
            source=None,
            selection_link=None,
            config=config,
        )
        return ResolvedExecutionContext(
            config=config,
            credentials=credentials,
            catalog=None,
            selection_link=None,
            source=None,
        )

    async def get_runtime_credentials_for_config(
        self,
        config: AgentConfig,
    ) -> dict[str, str] | None:
        return (await self.resolve_execution_context(config)).credentials

    async def resolve_runtime_agent_config(
        self,
        config: AgentConfig,
    ) -> AgentConfig:
        return (await self.resolve_execution_context(config)).config

    @contextlib.contextmanager
    def credentials_sandbox(self, credentials: dict[str, str]) -> Iterator[None]:
        secrets_token = registry_secrets.set_context(credentials)
        try:
            with secrets_manager.env_sandbox(credentials):
                yield
        finally:
            registry_secrets.reset_context(secrets_token)

    @contextlib.asynccontextmanager
    async def with_model_config(
        self,
        *,
        selection: ModelSelection | None = None,
    ) -> AsyncIterator[AgentConfig]:
        if selection is None:
            default_selection = await self.selections.get_default_model()
            if default_selection is None:
                raise TracecatNotFoundError("No default model set")
            selection = default_selection
        context = await self._resolve_enabled_context(
            selection,
            workspace_id=self.role.workspace_id,
            overrides=AgentConfig(
                source_id=selection.source_id,
                model_name=selection.model_name,
                model_provider=selection.model_provider,
            ),
        )
        with self.credentials_sandbox(context.credentials):
            yield context.config

    @contextlib.asynccontextmanager
    async def with_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> AsyncIterator[AgentConfig]:
        if self.presets is None:
            raise TracecatAuthorizationError("Agent presets require a workspace role.")
        preset_config = await self.presets.resolve_agent_preset_config(
            preset_id=preset_id,
            slug=slug,
            preset_version_id=preset_version_id,
            preset_version=preset_version,
        )
        context = await self.resolve_execution_context(
            preset_config,
            workspace_id=self.role.workspace_id,
        )
        if context.catalog is None:
            if not context.credentials:
                raise TracecatNotFoundError(
                    f"No credentials found for provider '{preset_config.model_provider}'. "
                    "Please configure credentials for this provider first."
                )
        with self.credentials_sandbox(context.credentials):
            yield context.config

    @require_scope("agent:read")
    async def get_provider_credentials(self, provider: str) -> dict[str, str] | None:
        return await self._load_provider_credentials(provider)
