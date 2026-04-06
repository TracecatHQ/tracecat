"""Credential resolver interfaces for the Tracecat LLM proxy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from aiocache import Cache, cached

from tracecat import config
from tracecat.agent.llm_proxy.provider_bedrock import assume_bedrock_aws_role
from tracecat.agent.service import AgentManagementService
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers import InternalServiceID, OrganizationID, WorkspaceID


class CredentialResolver(Protocol):
    """Resolve provider credentials for a request."""

    async def resolve(
        self,
        provider: str,
        workspace_id: WorkspaceID,
        organization_id: OrganizationID,
        use_workspace_credentials: bool,
    ) -> dict[str, str] | None:
        """Return provider credentials or ``None`` if no credentials exist."""


@dataclass(slots=True)
class StaticCredentialResolver:
    """Resolver that always returns the same credential mapping (tests)."""

    credentials: dict[str, str] | None

    async def resolve(
        self,
        provider: str,
        workspace_id: WorkspaceID,
        organization_id: OrganizationID,
        use_workspace_credentials: bool,
    ) -> dict[str, str] | None:
        del provider, workspace_id, organization_id, use_workspace_credentials
        if self.credentials is None:
            return None
        return dict(self.credentials)


def _credential_cache_key_builder(
    func: Any,
    provider: str,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    use_workspace_credentials: bool,
    *,
    service_id: InternalServiceID = "tracecat-llm-gateway",
    **_kwargs: Any,
) -> str:
    del func
    scope = "workspace" if use_workspace_credentials else "organization"
    return f"llm_creds:{organization_id}:{workspace_id}:{scope}:{provider}:{service_id}"


@cached(
    ttl=int(config.TRACECAT__LLM_GATEWAY_CREDENTIAL_CACHE_TTL_SECONDS),
    cache=Cache.MEMORY,
    key_builder=_credential_cache_key_builder,
)
async def _resolve_credentials_cached(
    provider: str,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    use_workspace_credentials: bool,
    *,
    service_id: InternalServiceID = "tracecat-llm-gateway",
) -> dict[str, str] | None:
    """Resolve credentials through AgentManagementService with aiocache TTL.

    For Bedrock with AWS_ROLE_ARN, assumes the role and returns credentials
    with the assumed role's access key, secret key, and session token.
    """
    role = Role(
        type="service",
        user_id=None,
        service_id=service_id,
        workspace_id=workspace_id,
        organization_id=organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES[service_id],
    )
    async with AgentManagementService.with_session(role=role) as service:
        creds = await service.get_runtime_provider_credentials(
            provider,
            use_workspace_credentials=use_workspace_credentials,
        )
    if creds is None:
        return None
    #  Assume role for Bedrock if bearer token is not available
    if provider == "bedrock" and not creds.get("AWS_BEARER_TOKEN_BEDROCK"):
        creds = await assume_bedrock_aws_role(creds)
    return creds


@dataclass(slots=True)
class AgentManagementCredentialResolver:
    """Resolve provider credentials through AgentManagementService.

    Uses aiocache in-memory TTL cache (worker-scoped) to avoid repeated
    DB lookups for the same provider+workspace within the TTL window.
    """

    service_id: InternalServiceID = "tracecat-llm-gateway"

    async def resolve(
        self,
        provider: str,
        workspace_id: WorkspaceID,
        organization_id: OrganizationID,
        use_workspace_credentials: bool,
    ) -> dict[str, str] | None:
        return await _resolve_credentials_cached(
            provider,
            workspace_id,
            organization_id,
            use_workspace_credentials,
            service_id=self.service_id,
        )
