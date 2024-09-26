"""Use this in worker to execute actions."""

from typing import Any

import httpx
import orjson

from tracecat import config
from tracecat.clients import AuthenticatedServiceClient
from tracecat.contexts import ctx_role
from tracecat.dsl.models import UDFActionInput
from tracecat.logger import logger
from tracecat.registry.models import ArgsT, RegisteredUDFRead
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryActionError, RegistryError


class _RegistryHTTPClient(AuthenticatedServiceClient):
    """Async httpx client for the registry service."""

    def __init__(self, role: Role | None = None, *args, **kwargs):
        self._registry_base_url = config.TRACECAT__API_URL
        # Parent class sets the role
        logger.info("Initializing registry client", role=role)
        super().__init__(*args, role=role, base_url=self._registry_base_url, **kwargs)
        logger.info("Registry client initialized", role=role)
        self.params = self.params.add("workspace_id", str(role.workspace_id))
        logger.info("Added workspace id to registry client", role=role)


class RegistryClient:
    """Use this to interact with the remote registry service."""

    def __init__(self, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="remote-registry", role=self.role)

    """Execution"""

    async def call_action(self, input: UDFActionInput[ArgsT]) -> httpx.Response:
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

        role = input.role
        key = input.task.action
        content = input.model_dump_json()
        workspace_id = str(role.workspace_id) if role.workspace_id else None
        logger.info(
            f"Calling action {key!r} with content",
            content=content,
            role=role,
        )
        try:
            async with _RegistryHTTPClient(self.role) as client:
                logger.info(
                    f"Calling action {key!r} with client",
                    headers=client.headers,
                    params=client.params,
                )
                response = await client.post(
                    f"/registry-executor/{key}",
                    # NOTE(perf): Maybe serialize with orjson.dumps instead
                    headers={
                        "Content-Type": "application/json",
                        # Custom headers
                        **role.to_headers(),
                    },
                    content=content,
                    params={"workspace_id": workspace_id},
                )
            response.raise_for_status()
            return orjson.loads(response.content)
        except httpx.HTTPStatusError as e:
            if response.status_code == 404:
                raise RegistryActionError(
                    f"Action {key!r} not found in registry"
                ) from e
            elif response.status_code / 100 == 5:
                raise RegistryActionError(
                    f"The registry server returned a {response.status_code} error for action {key!r}: {e}"
                ) from e
            else:
                raise RegistryActionError(
                    f"Unexpected HTTP {response.status_code} error calling action {key!r} in registry: {e}"
                ) from e
        except orjson.JSONDecodeError as e:
            raise RegistryActionError(
                f"Error decoding JSON response for action {key!r}: {e}"
            ) from e
        except Exception as e:
            raise RegistryActionError(
                f"Unexpected error calling action {key!r} in registry: {e}"
            ) from e

    """Validation"""

    async def validate_action(
        self, *, action_name: str, args: dict[str, Any], registry_version: str
    ) -> Any:
        """Validate an action."""
        try:
            logger.warning("Validating action")
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.post(
                    f"/registry-executor/{action_name}/validate",
                    json={"registry_version": registry_version, "args": args},
                )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RegistryError(
                f"Failed to list registries: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise RegistryError(
                f"Network error while listing registries: {str(e)}"
            ) from e
        except Exception as e:
            raise RegistryError(
                f"Unexpected error while listing registries: {str(e)}"
            ) from e

    """Registry management"""

    async def list_registries(self) -> list[str]:
        try:
            logger.warning("Listing registries")
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.get("/registry")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RegistryError(
                f"Failed to list registries: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise RegistryError(
                f"Network error while listing registries: {str(e)}"
            ) from e
        except Exception as e:
            raise RegistryError(
                f"Unexpected error while listing registries: {str(e)}"
            ) from e

    async def create_registry(self, *, version: str, name: str | None = None) -> Any:
        """If no name is provided, the version is used as the name."""
        try:
            response = await self.post(
                f"/registry/{version}", json={"version": version, "name": name}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                raise RegistryError(
                    f"Registry version {version!r} already exists"
                ) from e
            elif e.response.status_code == 400:
                raise RegistryError(
                    f"Invalid registry version or name: {e.response.text}"
                ) from e
            else:
                raise RegistryError(
                    f"Failed to create registry: HTTP {e.response.status_code}"
                ) from e
        except httpx.RequestError as e:
            raise RegistryError(
                f"Network error while creating registry: {str(e)}"
            ) from e
        except Exception as e:
            raise RegistryError(
                f"Unexpected error while creating registry: {str(e)}"
            ) from e

    async def get_registry(self, version: str) -> list[RegisteredUDFRead]:
        try:
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.get(f"/registry/{version}")
            response.raise_for_status()
            data = response.json()
            return [RegisteredUDFRead(**item) for item in data]
        except httpx.HTTPStatusError as e:
            raise RegistryError(
                f"Failed to list registry actions: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise RegistryError(
                f"Network error while listing registry actions: {str(e)}"
            ) from e
        except Exception as e:
            raise RegistryError(
                f"Unexpected error while listing registry actions: {str(e)}"
            ) from e

    """Development"""

    if config.TRACECAT__APP_ENV == "development":

        async def _register_test_module(
            self,
            *,
            version: str,
            code: str,
            module_name: str,
            validate_keys: list[str] | None = None,
        ):
            """Use this only for testing purposes."""
            if config.TRACECAT__APP_ENV != "development":
                # Unreachable
                raise RegistryError("This method is only available in development mode")
            try:
                async with _RegistryHTTPClient(role=self.role) as client:
                    response = await client.post(
                        "/test-registry",
                        params={"workspace_id": self.role.workspace_id},
                        json={
                            "version": version,
                            "code": code,
                            "module_name": module_name,
                            "validate_function_names": validate_keys,
                        },
                    )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                raise RegistryError(
                    f"Failed to register test module: HTTP {e.response.status_code}"
                    f"Response: {e.response.text}"
                ) from e
            except httpx.RequestError as e:
                raise RegistryError(
                    f"Network error while registering test module: {str(e)}"
                ) from e
