"""Use this in worker to execute actions."""

from collections.abc import Mapping
from typing import Any, cast

import httpx
import orjson

from tracecat import config
from tracecat.clients import AuthenticatedServiceClient
from tracecat.contexts import ctx_role
from tracecat.dsl.models import RunActionInput
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    RegistryActionErrorInfo,
    RegistryActionRead,
    RegistryActionValidateResponse,
)
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryActionError, RegistryError


class _RegistryHTTPClient(AuthenticatedServiceClient):
    """Async httpx client for the registry service."""

    def __init__(self, role: Role | None = None, *args: Any, **kwargs: Any) -> None:
        self._registry_base_url = config.TRACECAT__API_URL
        super().__init__(role, *args, base_url=self._registry_base_url, **kwargs)
        self.params = self.params.add("workspace_id", str(self.role.workspace_id))


class RegistryClient:
    """Use this to interact with the remote registry service."""

    _repos_endpoint = "/registry/repos"
    _actions_endpoint = "/registry/actions"
    _timeout: float = 60.0

    def __init__(self, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="remote-registry", role=self.role)

    """Execution"""

    async def call_action(self, input: RunActionInput) -> Any:
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
        logger.debug(
            f"Calling action {key!r} with content",
            content=content,
            role=role,
            timeout=self._timeout,
        )
        try:
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.post(
                    f"{self._actions_endpoint}/{key}/execute",
                    # NOTE(perf): Maybe serialize with orjson.dumps instead
                    headers={
                        "Content-Type": "application/json",
                        # Custom headers
                        **role.to_headers(),
                    },
                    content=content,
                    params={"workspace_id": workspace_id},
                    timeout=self._timeout,
                )
            response.raise_for_status()
            return orjson.loads(response.content)
        except httpx.HTTPStatusError as e:
            resp = e.response.json()
            if detail := resp.get("detail"):
                val_detail = RegistryActionErrorInfo(**detail)
                detail = str(val_detail)
            else:
                detail = e.response.text
            logger.error("Registry returned an error", error=e, detail=detail)
            if e.response.status_code / 100 == 5:
                raise RegistryActionError(
                    f"There was an error in the registry when calling action {key!r} ({e.response.status_code}).\n\n{detail}"
                ) from e
            else:
                raise RegistryActionError(
                    f"Unexpected registry error ({e.response.status_code}):\n\n{e}\n\n{detail}"
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
        self, *, action_name: str, args: Mapping[str, Any]
    ) -> RegistryActionValidateResponse:
        """Validate an action."""
        try:
            logger.warning("Validating action")
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.post(
                    f"{self._actions_endpoint}/{action_name}/validate",
                    json={"args": args},
                )
            response.raise_for_status()
            return RegistryActionValidateResponse.model_validate_json(response.content)
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

    async def list_repositories(self) -> list[str]:
        try:
            logger.warning("Listing registries")
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.get(self._repos_endpoint)
            response.raise_for_status()
            return cast(list[str], response.json())
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

    async def get_repository_actions(self, version: str) -> list[RegistryActionRead]:
        try:
            async with _RegistryHTTPClient(self.role) as client:
                response = await client.get(f"{self._repos_endpoint}/{version}")
            response.raise_for_status()
            data = response.json()
            return [RegistryActionRead(**item) for item in data]
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
        ) -> dict[str, Any]:
            """Use this only for testing purposes."""
            if config.TRACECAT__APP_ENV != "development":
                # Unreachable
                raise RegistryError("This method is only available in development mode")
            try:
                async with _RegistryHTTPClient(role=self.role) as client:
                    response = await client.post(
                        "/test-registry",
                        params={"workspace_id": str(self.role.workspace_id)},
                        json={
                            "version": version,
                            "code": code,
                            "module_name": module_name,
                            "validate_function_names": validate_keys,
                        },
                    )
                response.raise_for_status()
                return cast(dict[str, Any], response.json())
            except httpx.HTTPStatusError as e:
                raise RegistryError(
                    f"Failed to register test module: HTTP {e.response.status_code}"
                    f"Response: {e.response.text}"
                ) from e
            except httpx.RequestError as e:
                raise RegistryError(
                    f"Network error while registering test module: {str(e)}"
                ) from e
