"""Domain types for LLM provider management."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class AgentCustomProviderDiscoveryStatus(StrEnum):
    """Discovery lifecycle states for custom provider catalog refreshes."""

    NEVER = "never"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(kw_only=True, slots=True)
class ResolvedCustomProviderCredentials:
    """Decrypted credentials for a custom LLM provider."""

    api_key: str | None = None
    custom_headers: dict[str, str] | None = None


@dataclass(kw_only=True, slots=True)
class ResolvedCatalogConfig:
    """Access-validated config for a catalog selection."""

    catalog_id: UUID
    organization_id: UUID | None
    model_provider: str
    model_name: str
    custom_provider_id: UUID | None = None
    base_url: str | None = None
    passthrough: bool = False
    api_key_header: str | None = None
    custom_provider_credentials: ResolvedCustomProviderCredentials | None = None
