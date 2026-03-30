"""Credential resolver interfaces for the Tracecat LLM proxy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from aiocache import Cache, cached
from sqlalchemy import select

from tracecat import config
from tracecat.agent.credentials.service import AgentCredentialsService
from tracecat.agent.provider_config import (
    deserialize_source_config,
    map_source_credentials,
    source_runtime_base_url,
)
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import AgentSource
from tracecat.identifiers import InternalServiceID, OrganizationID, WorkspaceID

if TYPE_CHECKING:
    from tracecat.agent.tokens import LLMTokenClaims


class CredentialResolver(Protocol):
    """Resolve provider credentials for a request."""

    async def resolve(
        self,
        claims: LLMTokenClaims,
    ) -> dict[str, str] | None:
        """Return provider credentials or ``None`` if no credentials exist."""


@dataclass(slots=True)
class StaticCredentialResolver:
    """Resolver that always returns the same credential mapping (tests)."""

    credentials: dict[str, str] | None

    async def resolve(
        self,
        claims: LLMTokenClaims,
    ) -> dict[str, str] | None:
        del claims
        if self.credentials is None:
            return None
        return dict(self.credentials)


# ---------------------------------------------------------------------------
# Org-level provider credential resolution (built-in providers)
# ---------------------------------------------------------------------------


def _credential_cache_key_builder(
    func: Any,
    provider: str,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    *,
    service_id: InternalServiceID = "tracecat-llm-gateway",
    **_kwargs: Any,
) -> str:
    del func
    return f"llm_creds:{organization_id}:{workspace_id}:organization:{provider}:{service_id}"


@cached(
    ttl=int(config.TRACECAT__LLM_GATEWAY_CREDENTIAL_CACHE_TTL_SECONDS),
    cache=Cache.MEMORY,
    key_builder=_credential_cache_key_builder,
)
async def _resolve_credentials_cached(
    provider: str,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    *,
    service_id: InternalServiceID = "tracecat-llm-gateway",
) -> dict[str, str] | None:
    """Resolve credentials through AgentCredentialsService with aiocache TTL."""
    role = Role(
        type="service",
        user_id=None,
        service_id=service_id,
        workspace_id=workspace_id,
        organization_id=organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES[service_id],
    )
    async with AgentCredentialsService.with_session(role=role) as service:
        return await service.get_provider_credentials(provider)


# ---------------------------------------------------------------------------
# Source-backed credential resolution (custom sources)
# ---------------------------------------------------------------------------


def _source_credential_cache_key_builder(
    func: Any,
    source_id: uuid.UUID,
    provider: str,
    organization_id: OrganizationID,
    *,
    service_id: InternalServiceID = "tracecat-llm-gateway",
    **_kwargs: Any,
) -> str:
    del func
    return f"llm_creds:{organization_id}:source:{source_id}:{provider}:{service_id}"


@cached(
    ttl=int(config.TRACECAT__LLM_GATEWAY_CREDENTIAL_CACHE_TTL_SECONDS),
    cache=Cache.MEMORY,
    key_builder=_source_credential_cache_key_builder,
)
async def _resolve_source_credentials_cached(
    source_id: uuid.UUID,
    provider: str,
    organization_id: OrganizationID,
    *,
    service_id: InternalServiceID = "tracecat-llm-gateway",
) -> dict[str, str] | None:
    """Resolve credentials from an AgentSource's encrypted config.

    Args:
        source_id: The custom source UUID.
        provider: The model_provider value from the catalog entry.
        organization_id: Org scope for security.
        service_id: Internal service identifier (for cache partitioning).

    Returns:
        Credential dict with provider-expected keys, or ``None`` if the
        source does not exist.
    """
    del service_id  # used only by cache key builder
    async with get_async_session_context_manager() as session:
        stmt = select(AgentSource).where(
            AgentSource.id == source_id,
            AgentSource.organization_id == organization_id,
        )
        source = (await session.execute(stmt)).scalar_one_or_none()
        if source is None:
            return None
        source_config = deserialize_source_config(source.encrypted_config)
        runtime_base_url = source_runtime_base_url(source, source_config=source_config)
        return map_source_credentials(
            source=source,
            source_config=source_config,
            model_provider=provider,
            runtime_base_url=runtime_base_url,
        )


# ---------------------------------------------------------------------------
# Composite resolver
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentCredentialResolver:
    """Resolve provider credentials through AgentCredentialsService.

    For built-in providers, uses aiocache in-memory TTL cache (worker-scoped)
    to avoid repeated DB lookups for the same provider+workspace.

    For source-backed models (``claims.source_id`` is set), loads credentials
    from the ``AgentSource.encrypted_config`` instead of org-level secrets.
    """

    service_id: InternalServiceID = "tracecat-llm-gateway"

    async def resolve(
        self,
        claims: LLMTokenClaims,
    ) -> dict[str, str] | None:
        if claims.source_id is not None:
            return await _resolve_source_credentials_cached(
                claims.source_id,
                claims.provider,
                claims.organization_id,
                service_id=self.service_id,
            )
        return await _resolve_credentials_cached(
            claims.provider,
            claims.workspace_id,
            claims.organization_id,
            service_id=self.service_id,
        )
