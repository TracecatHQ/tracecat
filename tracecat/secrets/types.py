"""Domain types for workspace secret resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from tracecat.secrets.enums import (
    AwsSecretResolutionErrorCode,
    SecretStoreProvider,
)

if TYPE_CHECKING:
    from tracecat.secrets.schemas import AwsSecretKeyMapping, SecretStoreConfig


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a provider reference check without any remote secret values."""

    ok: bool
    error_code: AwsSecretResolutionErrorCode | None = None
    provider_error_code: str | None = None
    resolved_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExternalSecretReference:
    """Immutable descriptor for one externally backed workspace secret alias.

    Materialized from ORM rows so the database session can be released before
    any remote call happens. Carries no remote values.
    """

    secret_id: UUID
    alias: str
    environment: str
    store_id: UUID
    provider: SecretStoreProvider
    store_enabled: bool
    store_config: SecretStoreConfig
    key: str
    mapping: AwsSecretKeyMapping

    @property
    def fetch_key(self) -> tuple[UUID, str]:
        """Deduplication key for one remote read within an operation."""
        return (self.store_id, self.key)
