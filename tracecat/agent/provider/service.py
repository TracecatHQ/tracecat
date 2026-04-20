"""Service for managing custom LLM providers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import orjson
import sqlalchemy as sa
from sqlalchemy import select

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderRead,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.types import (
    ResolvedCatalogConfig,
    ResolvedCustomProviderCredentials,
)
from tracecat.audit.logger import audit_log
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentCatalog, AgentCustomProvider, AgentModelAccess
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseOrgService

CUSTOM_MODEL_PROVIDER_SLUG = "custom-model-provider"


class AgentCustomProviderService(BaseOrgService):
    """Manage organization-scoped custom LLM provider configurations."""

    service_name = "agent_custom_provider"

    @require_scope("agent:create")
    @audit_log(resource_type="agent_custom_provider", action="create")
    async def create_provider(
        self,
        provider: AgentCustomProviderCreate,
    ) -> AgentCustomProviderRead:
        """Create a new custom LLM provider for the organization."""
        encrypted_config = None
        secrets_dict: dict[str, object] = {}
        if provider.api_key:
            secrets_dict["api_key"] = provider.api_key
        if provider.custom_headers:
            secrets_dict["custom_headers"] = provider.custom_headers

        if secrets_dict:
            encrypted_config = encrypt_value(
                orjson.dumps(secrets_dict),
                key=get_db_encryption_key(),
            )

        model = AgentCustomProvider(
            organization_id=self.organization_id,
            display_name=provider.display_name,
            base_url=provider.base_url,
            passthrough=provider.passthrough,
            api_key_header=provider.api_key_header,
            encrypted_config=encrypted_config,
        )
        self.session.add(model)
        await self.session.commit()
        return AgentCustomProviderRead.model_validate(model)

    @require_scope("agent:read")
    async def get_provider(self, provider_id: UUID) -> AgentCustomProviderRead:
        """Get a specific provider by id scoped to this organization."""
        stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )
        return AgentCustomProviderRead.model_validate(model)

    @require_scope("agent:read")
    async def list_providers(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[AgentCustomProviderRead]:
        """List custom providers with cursor pagination."""
        stmt = (
            select(AgentCustomProvider)
            .where(AgentCustomProvider.organization_id == self.organization_id)
            .order_by(
                AgentCustomProvider.created_at.desc(),
                AgentCustomProvider.id.desc(),
            )
        )

        paginator = BaseCursorPaginator(self.session)
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
            except (ValueError, AttributeError) as err:
                raise ValueError("Invalid cursor") from err
            if not isinstance(cursor_data.sort_value, datetime):
                raise ValueError("Invalid cursor")
            stmt = stmt.where(
                sa.or_(
                    AgentCustomProvider.created_at < cursor_data.sort_value,
                    sa.and_(
                        AgentCustomProvider.created_at == cursor_data.sort_value,
                        AgentCustomProvider.id < UUID(cursor_data.id),
                    ),
                )
            )

        items = (
            (await self.session.execute(stmt.limit(params.limit + 1))).scalars().all()
        )
        has_more = len(items) > params.limit
        if has_more:
            items = items[: params.limit]

        next_cursor = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = paginator.encode_cursor(
                last_item.id,
                sort_column="created_at",
                sort_value=last_item.created_at,
            )

        return CursorPaginatedResponse(
            items=[AgentCustomProviderRead.model_validate(m) for m in items],
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def _decrypt_custom_provider_credentials(
        self,
        provider: AgentCustomProvider,
    ) -> ResolvedCustomProviderCredentials | None:
        """Decrypt the provider's stored execution credentials, if any."""
        if provider.encrypted_config is None:
            return None

        decrypted = decrypt_value(
            provider.encrypted_config,
            key=get_db_encryption_key(),
        )
        raw_config = orjson.loads(decrypted)
        if not isinstance(raw_config, dict):
            return None

        api_key = raw_config.get("api_key")
        custom_headers = raw_config.get("custom_headers")
        resolved_headers: dict[str, str] | None = None
        if isinstance(custom_headers, dict):
            resolved_headers = {
                key: value
                for key, value in custom_headers.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        return ResolvedCustomProviderCredentials(
            api_key=api_key if isinstance(api_key, str) else None,
            custom_headers=resolved_headers,
        )

    async def resolve_catalog_config(
        self,
        *,
        catalog_id: UUID,
        workspace_id: UUID | None = None,
    ) -> ResolvedCatalogConfig:
        """Resolve a catalog selection into an access-validated config."""
        org_access_exists = (
            sa.select(sa.literal(1))
            .select_from(AgentModelAccess)
            .where(
                AgentModelAccess.organization_id == self.organization_id,
                AgentModelAccess.workspace_id.is_(None),
                AgentModelAccess.catalog_id == AgentCatalog.id,
            )
            .exists()
        )

        effective_enabled_expr: sa.ColumnElement[bool]
        if workspace_id is None:
            effective_enabled_expr = org_access_exists
        else:
            workspace_override_exists = (
                sa.select(sa.literal(1))
                .select_from(AgentModelAccess)
                .where(
                    AgentModelAccess.organization_id == self.organization_id,
                    AgentModelAccess.workspace_id == workspace_id,
                )
                .exists()
            )
            workspace_access_exists = (
                sa.select(sa.literal(1))
                .select_from(AgentModelAccess)
                .where(
                    AgentModelAccess.organization_id == self.organization_id,
                    AgentModelAccess.workspace_id == workspace_id,
                    AgentModelAccess.catalog_id == AgentCatalog.id,
                )
                .exists()
            )
            effective_enabled_expr = sa.case(
                (workspace_override_exists, workspace_access_exists),
                else_=org_access_exists,
            )

        stmt = (
            sa.select(
                AgentCatalog,
                AgentCustomProvider,
                effective_enabled_expr.label("is_enabled"),
            )
            .outerjoin(
                AgentCustomProvider,
                sa.and_(
                    AgentCustomProvider.organization_id == AgentCatalog.organization_id,
                    AgentCustomProvider.id == AgentCatalog.custom_provider_id,
                ),
            )
            .where(AgentCatalog.id == catalog_id)
        )
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"Catalog entry {catalog_id} not found")

        catalog, provider, is_enabled = row
        if not is_enabled:
            scope = (
                f"workspace {workspace_id}"
                if workspace_id is not None
                else "organization"
            )
            raise TracecatValidationError(
                f"Catalog entry {catalog_id} is not enabled for {scope}"
            )

        return ResolvedCatalogConfig(
            catalog_id=catalog.id,
            organization_id=catalog.organization_id,
            model_provider=catalog.model_provider,
            model_name=catalog.model_name,
            custom_provider_id=catalog.custom_provider_id,
            base_url=provider.base_url if provider is not None else None,
            passthrough=provider.passthrough if provider is not None else False,
            api_key_header=provider.api_key_header if provider is not None else None,
            custom_provider_credentials=(
                self._decrypt_custom_provider_credentials(provider)
                if provider is not None
                else None
            ),
        )

    @require_scope("agent:update")
    @audit_log(
        resource_type="agent_custom_provider",
        action="update",
        resource_id_attr="provider_id",
    )
    async def update_provider(
        self,
        provider_id: UUID,
        updates: AgentCustomProviderUpdate,
    ) -> AgentCustomProviderRead:
        """Update custom provider configuration."""
        stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )

        update_data = updates.model_dump(exclude_unset=True)
        incoming_secrets: dict[str, object] = {}
        if "api_key" in update_data:
            incoming_secrets["api_key"] = update_data.pop("api_key")
        if "custom_headers" in update_data:
            incoming_secrets["custom_headers"] = update_data.pop("custom_headers")

        if incoming_secrets:
            existing_secrets: dict[str, object] = {}
            if model.encrypted_config is not None:
                decrypted = decrypt_value(
                    model.encrypted_config,
                    key=get_db_encryption_key(),
                )
                raw = orjson.loads(decrypted)
                if isinstance(raw, dict):
                    existing_secrets = raw

            merged_secrets = {**existing_secrets}
            for key, value in incoming_secrets.items():
                if value:
                    merged_secrets[key] = value
                else:
                    merged_secrets.pop(key, None)

            if merged_secrets:
                model.encrypted_config = encrypt_value(
                    orjson.dumps(merged_secrets),
                    key=get_db_encryption_key(),
                )
            else:
                model.encrypted_config = None

        for key, value in update_data.items():
            setattr(model, key, value)
        await self.session.commit()
        await self.session.refresh(model)
        return AgentCustomProviderRead.model_validate(model)

    @require_scope("agent:delete")
    @audit_log(
        resource_type="agent_custom_provider",
        action="delete",
        resource_id_attr="provider_id",
    )
    async def delete_provider(self, provider_id: UUID) -> None:
        """Delete a custom provider and cascade its catalog rows."""
        stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        model = (await self.session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )
        await self.session.delete(model)
        await self.session.commit()

    @require_scope("agent:update")
    @audit_log(
        resource_type="agent_custom_provider",
        action="update",
        resource_id_attr="provider_id",
    )
    async def refresh_provider_catalog(self, provider_id: UUID) -> None:
        """Discover models from the provider endpoint and upsert catalog rows."""
        provider_stmt = select(AgentCustomProvider).where(
            sa.and_(
                AgentCustomProvider.id == provider_id,
                AgentCustomProvider.organization_id == self.organization_id,
            )
        )
        provider = (await self.session.execute(provider_stmt)).scalar_one_or_none()
        if provider is None:
            raise TracecatNotFoundError(
                f"Custom provider {provider_id} not found in organization"
            )

        if not provider.base_url:
            raise ValueError("Provider base_url not configured")

        secrets_dict: dict[str, object] = {}
        if provider.encrypted_config:
            decrypted = decrypt_value(
                provider.encrypted_config,
                key=get_db_encryption_key(),
            )
            secrets_dict = orjson.loads(decrypted)

        api_key = secrets_dict.get("api_key")
        custom_headers = secrets_dict.get("custom_headers", {})

        models = await self._discover_models(
            provider.base_url,
            api_key=api_key if isinstance(api_key, str) else None,
            custom_headers=(
                custom_headers if isinstance(custom_headers, dict) else None
            ),
            api_key_header=provider.api_key_header,
        )

        catalog_service = AgentCatalogService(session=self.session)
        await catalog_service.upsert_discovered_models(
            org_id=self.organization_id,
            custom_provider_id=provider_id,
            model_provider=CUSTOM_MODEL_PROVIDER_SLUG,
            models=models,
        )

        provider.last_refreshed_at = datetime.now(UTC)
        await self.session.commit()

    async def validate_provider(
        self,
        base_url: str,
        api_key: str | None = None,
        api_key_header: str | None = None,
        custom_headers: dict[str, str] | None = None,
    ) -> bool:
        """Test provider connectivity."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = custom_headers.copy() if custom_headers else {}
                if api_key and api_key_header:
                    headers[api_key_header] = api_key
                response = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers=headers,
                )
                return response.status_code == 200
        except Exception:
            return False

    async def _discover_models(
        self,
        base_url: str,
        api_key: str | None = None,
        custom_headers: dict[str, str] | None = None,
        api_key_header: str | None = None,
    ) -> list[dict[str, object]]:
        """Discover available models from a provider endpoint."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = custom_headers.copy() if custom_headers else {}
                if api_key and api_key_header:
                    headers[api_key_header] = api_key
                response = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    return [item for item in data["data"] if isinstance(item, dict)]
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                raise ValueError(f"Unexpected response format: {type(data)}")
        except httpx.HTTPError as err:
            raise ValueError(f"Failed to discover models: {err}") from err
