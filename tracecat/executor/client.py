"""Use this in worker to execute actions."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from json import JSONDecodeError
from typing import Any, cast

import httpx
import orjson
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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
from tracecat.store.models import ActionRefHandle
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryActionError, RegistryError


class ExecutorHTTPClient(AuthenticatedServiceClient):
    """Async httpx client for the executor service."""

    def __init__(self, role: Role | None = None, *args: Any, **kwargs: Any) -> None:
        self._executor_base_url = config.TRACECAT__EXECUTOR_URL
        super().__init__(role, *args, base_url=self._executor_base_url, **kwargs)
        self.params = self.params.add(
            "workspace_id", str(self.role.workspace_id) if self.role else None
        )


class ExecutorClient:
    """Use this to interact with the remote executor service."""

    _timeout: float = 60.0

    def __init__(self, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="executor-client", role=self.role)

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[ExecutorHTTPClient]:
        async with ExecutorHTTPClient(self.role) as client:
            yield client

    """Execution"""

    async def run_action_memory_backend(self, input: RunActionInput) -> Any:
        action_type = input.task.action
        content = input.model_dump_json()
        logger.debug(
            f"Calling action {action_type!r} with content",
            content=content,
            role=self.role,
            timeout=self._timeout,
        )
        try:
            async with self._client() as client:
                # No need to include role headers here because it's already
                # added in AuthenticatedServiceClient
                response = await client.post(
                    f"/run/{action_type}",
                    headers={"Content-Type": "application/json"},
                    content=content,
                    timeout=self._timeout,
                )
            response.raise_for_status()
            return orjson.loads(response.content)
        except httpx.HTTPStatusError as e:
            logger.info("Handling registry error", error=e)
            try:
                resp = e.response.json()
            except JSONDecodeError:
                logger.warning("Failed to decode JSON response, returning empty dict")
                resp = {}
            if (
                isinstance(resp, Mapping)
                and (detail := resp.get("detail"))
                and isinstance(detail, Mapping)
            ):
                val_detail = RegistryActionErrorInfo(**detail)
                detail = str(val_detail)
            else:
                detail = e.response.text
            logger.error("Registry returned an error", error=e, detail=detail)
            if e.response.status_code / 100 == 5:
                raise RegistryActionError(
                    f"There was an error in the registry when calling action {action_type!r} ({e.response.status_code}).\n\n{detail}"
                ) from e
            else:
                raise RegistryActionError(
                    f"Unexpected registry error ({e.response.status_code}):\n\n{e}\n\n{detail}"
                ) from e
        except httpx.ReadTimeout as e:
            raise RegistryActionError(
                f"Timeout calling action {action_type!r} in registry: {e}"
            ) from e
        except orjson.JSONDecodeError as e:
            raise RegistryActionError(
                f"Error decoding JSON response for action {action_type!r}: {e}"
            ) from e
        except Exception as e:
            raise RegistryActionError(
                f"Unexpected error calling action {action_type!r} in registry: {e}"
            ) from e

    async def run_action_store_backend(self, input: RunActionInput) -> ActionRefHandle:
        action_type = input.task.action
        content = input.model_dump_json()
        logger.debug(
            f"Calling action {action_type!r} in store mode with content",
            content=content,
            role=self.role,
            timeout=self._timeout,
        )
        async with ExecutorHTTPClient(self.role) as client:
            # No need to include role headers here because it's already
            # added in AuthenticatedServiceClient
            response = await client.post(
                f"/run-store/{action_type}",
                headers={"Content-Type": "application/json"},
                content=content,
                timeout=self._timeout,
            )
        response.raise_for_status()
        return ActionRefHandle.model_validate_json(response.content)

    """Validation"""

    async def validate_action(
        self, *, action_name: str, args: Mapping[str, Any]
    ) -> RegistryActionValidateResponse:
        """Validate an action."""
        try:
            logger.warning("Validating action")
            async with self._client() as client:
                response = await client.post(
                    f"/validate/{action_name}", json={"args": args}
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

    """Management"""

    async def sync_executor(self, origin: str, *, max_attempts: int = 3) -> None:
        """Sync the executor from the registry.

        Args:
            origin: The origin of the sync request

        Raises:
            RegistryError: If the sync fails after all retries
        """

        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=4, max=10),
            retry=retry_if_exception_type(
                (
                    httpx.HTTPStatusError,
                    httpx.RequestError,
                    httpx.TimeoutException,
                    httpx.ConnectError,
                )
            ),
        )
        async def _sync_request() -> None:
            try:
                async with self._client() as client:
                    response = await client.post("/sync", json={"origin": origin})
                    response.raise_for_status()
            except Exception as e:
                logger.error("Error syncing executor", error=e)
                raise

        try:
            logger.info("Syncing executor", origin=origin)
            _ = await _sync_request()
        except httpx.HTTPStatusError as e:
            raise RegistryError(
                f"Failed to sync executor: HTTP {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise RegistryError(
                f"Network error while syncing executor: {str(e)}"
            ) from e
        except Exception as e:
            raise RegistryError(
                f"Unexpected error while syncing executor: {str(e)}"
            ) from e

    """Registry management"""

    async def list_repositories(self) -> list[str]:
        """XXX: This is not used anywhere."""
        try:
            logger.warning("Listing registries")
            async with ExecutorHTTPClient(self.role) as client:
                response = await client.get("/repos")
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
        """XXX: This is not used anywhere."""
        try:
            async with ExecutorHTTPClient(self.role) as client:
                response = await client.get(f"/repos/{version}")
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
            """XXX: This is not used anywhere."""
            if config.TRACECAT__APP_ENV != "development":
                # Unreachable
                raise RegistryError("This method is only available in development mode")
            try:
                async with ExecutorHTTPClient(role=self.role) as client:
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
