"""Secrets SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class SecretsClient:
    """Client for Secrets API operations.

    Note: This client is primarily used for searching/retrieving secrets
    that are needed by UDF actions. Creating/updating secrets should
    typically be done through the Tracecat UI.
    """

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def search(
        self,
        *,
        environment: str,
        names: list[str] | Unset = UNSET,
        ids: list[str] | Unset = UNSET,
        types: list[str] | Unset = UNSET,
    ) -> list[dict[str, Any]]:
        """Search secrets by criteria.

        Args:
            environment: Environment name (e.g., "default", "production").
            names: Filter by secret names.
            ids: Filter by secret IDs.
            types: Filter by secret types.

        Returns:
            List of matching secrets with their keys (decrypted).
        """
        params: dict[str, Any] = {"environment": environment}
        if is_set(names):
            params["name"] = names
        if is_set(ids):
            params["id"] = ids
        if is_set(types):
            params["type"] = types

        return await self._client.get("/secrets/search", params=params)

    async def list_secrets(
        self,
        *,
        types: list[str] | Unset = UNSET,
    ) -> list[dict[str, Any]]:
        """List all secrets in the workspace.

        Args:
            types: Filter by secret types.

        Returns:
            List of secrets (minimal info, no values).
        """
        params: dict[str, Any] = {}
        if is_set(types):
            params["type"] = types

        return await self._client.get("/secrets", params=params)

    async def get_secret(self, secret_name: str) -> dict[str, Any]:
        """Get a secret by name.

        Args:
            secret_name: The secret name.

        Returns:
            Secret data with keys.
        """
        return await self._client.get(f"/secrets/{secret_name}")

    async def get_secret_value(
        self,
        secret_name: str,
        key: str,
        *,
        environment: str = "default",
    ) -> str | None:
        """Get a specific key value from a secret.

        This is a convenience method that searches for a secret
        and extracts a specific key's value.

        Args:
            secret_name: The secret name.
            key: The key within the secret.
            environment: Environment name.

        Returns:
            The key's value, or None if not found.
        """
        secrets = await self.search(environment=environment, names=[secret_name])
        if not secrets:
            return None

        secret = secrets[0]
        keys = secret.get("keys", [])
        for kv in keys:
            if isinstance(kv, dict) and kv.get("key") == key:
                return kv.get("value")
        return None
