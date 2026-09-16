"""Domain types for workspace secret resolution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from tracecat.secrets.enums import AwsSecretMappingMode


@dataclass(frozen=True, slots=True)
class AwsSecretJsonFieldSelector:
    """Output key populated from a top-level JSON field."""

    key: str
    field: str


@dataclass(frozen=True, slots=True)
class AwsSecretReference:
    """Immutable descriptor for one AWS-backed workspace secret alias.

    Materialized from ORM rows so the database session can be released before
    any remote call happens. Carries no remote values.
    """

    secret_id: UUID
    alias: str
    environment: str
    store_id: UUID
    store_enabled: bool
    role_arn: str
    external_id: str
    region: str
    secret_arn: str
    mapping_mode: AwsSecretMappingMode
    whole_string_key: str | None
    json_fields: tuple[AwsSecretJsonFieldSelector, ...]

    @property
    def fetch_key(self) -> tuple[str, str]:
        """Deduplication key for one remote read within an operation."""
        return (self.role_arn, self.secret_arn)

    def output_keys(self) -> list[str]:
        """Return declared output key names without touching AWS."""
        if self.mapping_mode == AwsSecretMappingMode.WHOLE_STRING:
            return [self.whole_string_key] if self.whole_string_key else []
        return [selector.key for selector in self.json_fields]
