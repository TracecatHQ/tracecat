"""Domain types for workspace secret resolution and database encryption."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tracecat.secrets.enums import (
    AwsSecretResolutionErrorCode,
    SecretStoreProvider,
)

if TYPE_CHECKING:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import InstrumentedAttribute

    from tracecat.config import DBEncryptionAlgorithm
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


@dataclass(frozen=True, slots=True)
class DBEncryptionKey:
    """A database encryption key with its derived AES-256-GCM key and key id."""

    key_id: str
    fernet: Fernet
    aesgcm: AESGCM


@dataclass(frozen=True, slots=True)
class DecryptedToken:
    """Plaintext of a stored value plus the format and key that sealed it."""

    plaintext: bytes
    algorithm: DBEncryptionAlgorithm
    key_id: str


@dataclass(frozen=True, slots=True)
class EncryptedColumn:
    """A binary column that stores ``encrypt_value``/``encrypt_bytes`` output."""

    attr: InstrumentedAttribute[Any]
    only_if: ColumnElement[bool] | None = None

    @property
    def label(self) -> str:
        return f"{self.attr.parent.mapper.local_table.name}.{self.attr.key}"


@dataclass(frozen=True, slots=True)
class EncryptedJSONFields:
    """JSONB fields that store ``prefix + encrypt_value(...)`` as text."""

    attr: InstrumentedAttribute[Any]
    fields: tuple[str, ...]
    prefix: str

    @property
    def label(self) -> str:
        return f"{self.attr.parent.mapper.local_table.name}.{self.attr.key}"


@dataclass(slots=True)
class ReencryptReport:
    """Re-encrypt value counts for one column. Counts only, never contents."""

    target: str
    fernet: int = 0
    aes256gcm: int = 0
    current: int = 0
    undecryptable: int = 0
    rewritten: int = 0
    conflicts: int = 0


@dataclass(frozen=True, slots=True)
class ReencryptPass:
    """Settings for one scan or write pass over every encrypted column."""

    key: str
    write: bool
    skip_undecryptable: bool
    batch_size: int
