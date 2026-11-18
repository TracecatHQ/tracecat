"""Domain types for workspace secret resolution and database encryption."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tracecat.secrets.enums import (
    SecretResolutionErrorCode,
    SecretStoreProvider,
)

if TYPE_CHECKING:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import InstrumentedAttribute

    from tracecat.config import DBEncryptionAlgorithm
    from tracecat.secrets.schemas import SecretKeyMapping, SecretStoreConfig


@dataclass(frozen=True, slots=True)
class ActionSecrets:
    """Secrets resolved for one action invocation."""

    values: dict[str, Any] = field(repr=False)
    expires_at: datetime | None = None
    """When the earliest OAuth access token in `values` expires, if any."""
    partial: bool = False
    """True when an OAuth token lookup failed and was skipped, so a retry may
    return tokens that are missing from `values`."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a provider reference check without any remote secret values."""

    ok: bool
    error_code: SecretResolutionErrorCode | None = None
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
    mapping: SecretKeyMapping
    encrypted_access_token: bytes | None = field(default=None, repr=False)
    """The store's encrypted access token, if it has one. Decrypted only to fetch."""

    @property
    def fetch_key(self) -> tuple[UUID, str]:
        """Deduplication key for one remote read within an operation."""
        return (self.store_id, self.key)


@dataclass(frozen=True, slots=True)
class StoreContext:
    """One external store as a provider sees it during a fetch.

    ``access_token`` is the store's decrypted access token, ``None`` when the
    store has none.
    """

    store_id: UUID
    provider: SecretStoreProvider
    enabled: bool
    config: SecretStoreConfig
    access_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class Failure:
    """A sanitized provider failure for one remote reference.

    ``vendor_code`` is a short, provider-normalized identifier (an AWS error
    code, an HTTP status) for logs. It never carries vendor message text.
    """

    code: SecretResolutionErrorCode
    vendor_code: str | None = None


type FetchOutcome = str | Mapping[str, object] | Failure
"""One fetched value: a string, a field map (KV stores), or a failure."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcomes of one provider fetch, keyed by remote reference.

    ``release`` is an optional cleanup step (for example revoking a lease)
    that core awaits once the operation is done, on success and failure.
    """

    outcomes: Mapping[str, FetchOutcome] = field(repr=False)
    release: Callable[[], Awaitable[None]] | None = None


@dataclass(frozen=True, slots=True)
class DBEncryptionKey:
    """A database encryption key with its derived AES-256-GCM key and key id."""

    key_id: str
    fernet: Fernet
    aesgcm: AESGCM


@dataclass(frozen=True, slots=True)
class DecryptedToken:
    """Plaintext of a stored value plus the format and key that sealed it."""

    plaintext: bytes = field(repr=False)
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
