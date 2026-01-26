"""Variables SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


T = TypeVar("T")


class VariablesClient:
    """Client for Variables API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def get(
        self,
        name: str,
        key: str,
        *,
        environment: str | Unset = UNSET,
    ) -> Any:
        """Get a specific key's value from a workspace variable.

        Args:
            name: The variable name (e.g., "api_config").
            key: The key to retrieve from the variable's values (e.g., "base_url").
            environment: Optional environment filter.

        Returns:
            The value for the specified key.

        Raises:
            TracecatNotFoundError: If the variable doesn't exist.

        Example:
            >>> from tracecat_registry.context import get_context
            >>> base_url = await get_context().variables.get("api_config", "base_url")
        """
        params: dict[str, Any] = {"key": key}
        if is_set(environment):
            params["environment"] = environment
        return await self._client.get(f"/variables/{name}/value", params=params)

    async def get_or_default(
        self,
        name: str,
        key: str,
        default: T,
        *,
        environment: str | Unset = UNSET,
    ) -> Any | T:
        """Get a specific key's value from a workspace variable, or return a default.

        Args:
            name: The variable name (e.g., "api_config").
            key: The key to retrieve from the variable's values.
            default: Value to return if the variable or key doesn't exist.
            environment: Optional environment filter.

        Returns:
            The value for the specified key, or the default if not found.

        Example:
            >>> from tracecat_registry.context import get_context
            >>> timeout = await get_context().variables.get_or_default(
            ...     "api_config", "timeout", 30
            ... )
        """
        from tracecat_registry.sdk.exceptions import TracecatNotFoundError

        try:
            value = await self.get(name, key, environment=environment)
            # Return default if the value itself is None
            return default if value is None else value
        except TracecatNotFoundError:
            return default

    async def get_variable(
        self,
        name: str,
        *,
        environment: str | Unset = UNSET,
    ) -> dict[str, Any]:
        """Get a variable's full metadata by name.

        Args:
            name: The variable name (e.g., "api_config").
            environment: Optional environment filter.

        Returns:
            Variable metadata including id, name, description, values, and environment.

        Raises:
            TracecatNotFoundError: If the variable doesn't exist.
        """
        params: dict[str, Any] = {}
        if is_set(environment):
            params["environment"] = environment
        return await self._client.get(
            f"/variables/{name}", params=params if params else None
        )
