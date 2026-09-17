"""Domain types for workspace secret resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tracecat.secrets.enums import AwsSecretMappingMode

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]


class AwsSecretJsonField(BaseModel):
    """One declared output key sourced from a top-level JSON field."""

    model_config = ConfigDict(frozen=True)

    key: SecretKey = Field(..., min_length=1, max_length=255)
    field: str = Field(..., min_length=1, max_length=255)


class AwsSecretKeyMapping(BaseModel):
    """Declares how a remote AWS secret value maps onto output keys.

    ``whole_string`` maps the entire ``SecretString`` onto exactly one key.
    ``json`` maps selected top-level string fields onto declared keys.
    """

    model_config = ConfigDict(frozen=True)

    mode: AwsSecretMappingMode
    keys: list[SecretKey] = Field(default_factory=list, max_length=100)
    fields: list[AwsSecretJsonField] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_mapping(self) -> AwsSecretKeyMapping:
        if self.mode == AwsSecretMappingMode.WHOLE_STRING:
            if len(self.keys) != 1 or self.fields:
                raise ValueError(
                    "whole_string mappings declare exactly one output key and no fields"
                )
            return self
        if not self.fields or self.keys:
            raise ValueError("json mappings declare at least one field and no keys")
        output_keys = [entry.key for entry in self.fields]
        if len(set(output_keys)) != len(output_keys):
            raise ValueError("Output keys must be unique")
        return self

    def output_keys(self) -> list[str]:
        """Return the declared output key names without touching AWS."""
        if self.mode == AwsSecretMappingMode.WHOLE_STRING:
            return list(self.keys)
        return [entry.key for entry in self.fields]


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
    mapping: AwsSecretKeyMapping

    @property
    def fetch_key(self) -> tuple[UUID, str]:
        """Deduplication key for one remote read within an operation."""
        return (self.store_id, self.secret_arn)
