"""Resolve existing provider settings and pin embedding semantics per workspace."""

import hashlib
import uuid
from dataclasses import replace

import orjson
from pydantic import SecretStr
from sqlalchemy import exists, or_, select

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.models import (
    AgentCatalog,
    AgentModelAccess,
    OrganizationSecret,
    OrganizationSetting,
    SearchWorkspaceState,
    Workspace,
)
from tracecat.search.embeddings.catalog import (
    PROVIDER_ORDER,
    default_model,
    get_model,
    recipe_revision,
)
from tracecat.search.embeddings.types import (
    EmbeddingError,
    EmbeddingErrorCode,
    PinnedConfiguration,
    ResolvedCredential,
)
from tracecat.search.service import SearchStorage
from tracecat.search.types import SearchState
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues, decrypt_value


class EmbeddingSettingsStorage(SearchStorage):
    """Internal boundary for trusted indexing/query scopes, never caller-picked secrets."""

    async def current(self) -> tuple[int, SearchState, PinnedConfiguration | None]:
        """Read the saved pointer without creating state."""
        state = await self.session.scalar(
            select(SearchWorkspaceState).where(self._scope(SearchWorkspaceState))
        )
        if state is None:
            return 0, SearchState.DISABLED, None
        config = await self._configuration(state.current_version)
        if config is None:
            return state.current_version, SearchState(state.state), None
        spec = get_model(config.provider, config.model)
        # Restore persisted fields and carry the saved recipe revision separately.
        # synchronize() replaces stale/unknown revisions before any embedding call.
        spec = replace(
            spec,
            endpoint=config.endpoint or "",
            dimensions=config.dimensions,
            input_token_limit=config.input_token_limit,
        )
        return (
            config.version,
            SearchState(state.state),
            PinnedConfiguration(
                config.version,
                spec,
                config.credential_id,
                config.credential_environment,
                config.recipe_revision,
            ),
        )

    async def reconciliation_pending(self) -> bool:
        """Report durable rebuild work, including after selection has been pinned."""
        return bool(
            await self.session.scalar(
                select(SearchWorkspaceState.reconciliation_required).where(
                    self._scope(SearchWorkspaceState)
                )
            )
        )

    async def _preferred_provider(self) -> str | None:
        settings = (
            await self.session.scalars(
                select(OrganizationSetting).where(
                    OrganizationSetting.organization_id == self.scope.organization_id,
                    OrganizationSetting.key.in_(
                        ("agent_default_model_catalog_id", "agent_default_model")
                    ),
                )
            )
        ).all()
        values: dict[str, str] = {}
        for setting in settings:
            raw = (
                decrypt_value(setting.value, key=get_db_encryption_key())
                if setting.is_encrypted
                else setting.value
            )
            value = orjson.loads(raw)
            if isinstance(value, str):
                values[setting.key] = value
        stmt = select(AgentCatalog.model_provider).where(
            or_(
                AgentCatalog.organization_id == self.scope.organization_id,
                AgentCatalog.organization_id.is_(None),
            )
        )
        if catalog_id := values.get("agent_default_model_catalog_id"):
            try:
                identifier = uuid.UUID(catalog_id)
            except ValueError:
                return None
            return await self.session.scalar(stmt.where(AgentCatalog.id == identifier))
        if name := values.get("agent_default_model"):
            return await self.session.scalar(
                stmt.where(AgentCatalog.model_name == name)
                .order_by(AgentCatalog.model_provider, AgentCatalog.id)
                .limit(1)
            )
        return None

    async def available(self) -> tuple[PinnedConfiguration, ResolvedCredential] | None:
        """Choose only configured, permitted providers, independent of workflow secrets.

        Workspace model access overrides org access, as in the agent service.
        A malformed chosen credential fails explicitly; it never selects a fallback.
        """
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
            await self.session.scalars(
                select(AgentCatalog.model_provider)
                .join(AgentModelAccess, AgentModelAccess.catalog_id == AgentCatalog.id)
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
        ).all()
        preferred = await self._preferred_provider()
        providers = sorted(
            PROVIDER_ORDER, key=lambda p: (p != preferred, PROVIDER_ORDER.index(p))
        )
        for provider in providers:
            if provider not in allowed:
                continue
            secret = await self.session.scalar(
                select(OrganizationSecret)
                .where(
                    OrganizationSecret.organization_id == self.scope.organization_id,
                    OrganizationSecret.name == f"agent-{provider}-credentials",
                    OrganizationSecret.environment == DEFAULT_SECRETS_ENVIRONMENT,
                    OrganizationSecret.type == "custom",
                )
                .with_for_update(read=True)
            )
            if secret is None:
                continue
            try:
                keys = decrypt_keyvalues(
                    secret.encrypted_keys, key=get_db_encryption_key()
                )
                values = {item.key: item.value.get_secret_value() for item in keys}
                key_name = (
                    "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
                )
                if provider != "bedrock" and not values.get(key_name, "").strip():
                    raise ValueError("Missing provider key")
                if provider == "bedrock" and not (
                    values.get("AWS_ROLE_ARN")
                    or values.get("AWS_BEARER_TOKEN_BEDROCK")
                    or (
                        values.get("AWS_ACCESS_KEY_ID")
                        and values.get("AWS_SECRET_ACCESS_KEY")
                    )
                ):
                    raise ValueError("Missing Bedrock credentials")
                # A proxy key must never be sent to the public OpenAI endpoint.
                # Custom embedding endpoints are outside the supported catalog.
                if provider == "openai" and (
                    base_url := values.get("OPENAI_BASE_URL", "").strip()
                ):
                    if base_url.rstrip("/") != "https://api.openai.com/v1":
                        raise EmbeddingError(EmbeddingErrorCode.CONFIGURATION_INVALID)
                spec = default_model(provider, values.get("AWS_REGION"))
                credential = ResolvedCredential(
                    SecretStr(values.get(key_name, "")),
                    hashlib.sha256(secret.encrypted_keys).digest(),
                    values,
                )
            except EmbeddingError as exc:
                error = EmbeddingError(exc.code)
            except Exception:
                error = EmbeddingError(EmbeddingErrorCode.CREDENTIAL_INVALID)
            else:
                return PinnedConfiguration(
                    0, spec, secret.id, secret.environment, recipe_revision(spec)
                ), credential
            raise error
        return None

    async def synchronize(
        self,
    ) -> tuple[PinnedConfiguration, ResolvedCredential] | None:
        """Pin automatic selection before work; callers commit before provider I/O."""
        await self.lock_scope()
        selected = await self.available()
        _, state, current = await self.current()
        if selected is None:
            if current is not None:
                await self.set_state(SearchState.REINDEX_REQUIRED)
                await self.set_state(
                    SearchState.PAUSED
                    if state == SearchState.PAUSED
                    else SearchState.DISABLED
                )
            return None
        candidate, credential = selected
        if (
            current is None
            or current.recipe_revision != candidate.recipe_revision
            or recipe_revision(current.spec) != candidate.recipe_revision
        ):
            record = await self.save_configuration(
                provider=candidate.spec.provider,
                model=candidate.spec.model,
                endpoint=candidate.spec.endpoint,
                credential_id=candidate.credential_id,
                credential_environment=candidate.credential_environment,
                dimensions=candidate.spec.dimensions,
                input_token_limit=candidate.spec.input_token_limit,
                recipe_revision=candidate.recipe_revision,
            )
            current = replace(candidate, version=record.version)
        elif (
            current.credential_id != candidate.credential_id
            or current.credential_environment != candidate.credential_environment
        ):
            record = await self._configuration(current.version)
            assert record is not None
            record.credential_id = candidate.credential_id
            record.credential_environment = candidate.credential_environment
            current = replace(candidate, version=current.version)
        if state != SearchState.PAUSED:
            await self.set_state(SearchState.ACTIVE)
        return current, credential
