"""Domain types for workspace secret resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from tracecat.secrets.enums import (
    AwsSecretResolutionErrorCode,
    SecretStoreProvider,
)

if TYPE_CHECKING:
    from tracecat.secrets.schemas import AwsSecretKeyMapping, SecretStoreConfig

type CheckResult = tuple[
    bool, AwsSecretResolutionErrorCode | None, str | None, list[str]
]
"""``(ok, error_code, provider_error_code, resolved_keys)``."""


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
