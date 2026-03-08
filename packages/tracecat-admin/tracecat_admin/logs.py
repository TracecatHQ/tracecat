"""Local-only helpers for log search hashing."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum

LOG_HASH_VERSION = "v1"


class LogIdentifierType(StrEnum):
    """Supported identifier types for log search hashing."""

    EMAIL = "email"
    USERNAME = "username"
    EXTERNAL_ACCOUNT_ID = "external_account_id"


@dataclass(frozen=True)
class LogHashResult:
    """Computed log-search hash metadata."""

    identifier_type: LogIdentifierType
    field_name: str
    hash_value: str


def normalize_identifier_value(identifier_type: LogIdentifierType, value: str) -> str:
    """Normalize identifier values to match log redaction hashing."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("Identifier value cannot be empty")

    match identifier_type:
        case LogIdentifierType.EMAIL:
            return normalized.casefold()
        case LogIdentifierType.USERNAME | LogIdentifierType.EXTERNAL_ACCOUNT_ID:
            return normalized


def compute_log_search_hash(
    identifier_type: LogIdentifierType,
    value: str,
    *,
    key: str,
) -> LogHashResult:
    """Compute a deterministic versioned HMAC hash for log searching."""
    if not key:
        raise ValueError("Log redaction HMAC key cannot be empty")

    normalized = normalize_identifier_value(identifier_type, value)
    payload = f"{identifier_type.value}:{normalized}".encode()
    digest = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return LogHashResult(
        identifier_type=identifier_type,
        field_name=f"{identifier_type.value}_hash",
        hash_value=f"{LOG_HASH_VERSION}_{digest}",
    )
