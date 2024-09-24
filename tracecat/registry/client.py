"""Use this in worker to execute actions."""

from typing import Any

import httpx
import orjson

from tracecat import config
from tracecat.registry.models import ArgsT, RunActionParams
from tracecat.types.exceptions import RegistryUDFError


class RegistryClient(httpx.AsyncClient):
    """Async httpx client for the registry service."""

    def __init__(self, *args, **kwargs):
        self._registry_base_url = config.TRACECAT__API_URL + "/registry"
        super().__init__(*args, base_url=self._registry_base_url, **kwargs)

    async def call_action(
        self,
        *,
        key: str,
        version: str | None = None,
        args: ArgsT,
        context: dict[str, Any] | None = None,
        secrets: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """
        Call an action in the registry asynchronously.

        Parameters
        ----------
        key : str
            The unique identifier of the action to call.
        version : str | None, optional
            The version of the action to call. If None, the latest version is used.
        args : ArgsT
            The arguments to pass to the action.
        context : dict[str, Any] | None, optional
            Additional context information for the action. Not used in the current implementation.
        secrets : dict[str, Any] | None, optional
            Secrets to be used by the action. Not used in the current implementation.

        Returns
        -------
        httpx.Response
            The response from the registry service.

        Notes
        -----
        This method sends a POST request to the registry service to execute the specified action.
        The `context` and `secrets` parameters are currently not used in the implementation
        but are included in the method signature for potential future use.
        """

        params = {}
        if version:
            params["version"] = version
        body = RunActionParams(args=args, context=context)
        response = await self.post(
            f"/executor/{key}",
            headers={"Content-Type": "application/json"},
            # NOTE(perf): Maybe serialize with orjson.dumps instead
            content=body.model_dump_json(),
            params=params,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if response.status_code == 404:
                raise RegistryUDFError(f"Action {key!r} not found in registry") from e
            else:
                raise RegistryUDFError(
                    f"Error calling action {key} in registry: {e}"
                ) from e
        return orjson.loads(response.content)


if __name__ == "__main__":
    import asyncio

    async def main():
        client = RegistryClient()
        print(
            await client.call_action(
                key="core.transform.reshape", args={"name": "world"}
            )
        )

    asyncio.run(main())
