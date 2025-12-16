"""Variables SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class VariablesClient:
    """Client for Variables API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def get_variable(
        self,
        variable_name: str,
        *,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """Get a variable by name.

        Args:
            variable_name: The variable name.
            environment: Optional environment filter.

        Returns:
            Variable data with values.
        """
        params: dict[str, Any] = {}
        if environment is not None:
            params["environment"] = environment

        return await self._client.get(f"/variables/{variable_name}", params=params)

    async def get_variable_value(
        self,
        name: str,
        key: str,
        *,
        environment: str | None = None,
    ) -> Any | None:
        """Get a specific key value from a variable.

        This is a convenience method that fetches a variable
        and extracts a specific key's value.

        Args:
            name: The variable name.
            key: The key within the variable's values.
            environment: Optional environment filter.

        Returns:
            The key's value, or None if not found.
        """
        try:
            variable = await self.get_variable(name, environment=environment)
        except Exception:
            return None

        values = variable.get("values", {})
        if isinstance(values, dict):
            return values.get(key)
        return None

    async def list_variables(
        self,
        *,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all variables in the workspace.

        Args:
            environment: Optional environment filter.

        Returns:
            List of variables.
        """
        params: dict[str, Any] = {}
        if environment is not None:
            params["environment"] = environment

        return await self._client.get("/variables", params=params)
