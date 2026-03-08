from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteSecretRecord:
    """A remote secret materialized from an external backend."""

    name: str
    secret_string: str


@runtime_checkable
class CredentialSyncBackend(Protocol):
    """Provider-neutral interface for batch secret synchronization."""

    async def upsert_secret(self, *, secret_name: str, secret_string: str) -> bool:
        """Create or update a remote secret.

        Returns:
            True when the secret was created, False when it was updated.
        """
        ...

    async def list_secrets(self, *, prefix: str) -> Sequence[RemoteSecretRecord]:
        """List remote secrets under a deterministic prefix."""
        ...
