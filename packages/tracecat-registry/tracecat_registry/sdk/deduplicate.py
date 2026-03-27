"""Deduplicate SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class DeduplicateClient:
    """Client for workspace-scoped deduplication API."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def check_and_set(
        self,
        digests: list[str],
        expire_seconds: int,
    ) -> list[bool]:
        """Atomically check and set deduplication digests.

        Args:
            digests: SHA256 hex digests to check.
            expire_seconds: TTL for each digest key.

        Returns:
            List of booleans aligned to input order. True means the digest
            was newly inserted; False means it already existed.
        """
        resp = await self._client.post(
            "/deduplicate/check-and-set",
            json={"digests": digests, "expire_seconds": expire_seconds},
        )
        return resp["inserted"]
