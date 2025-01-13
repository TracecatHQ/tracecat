"""Use this in worker to execute actions."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from json import JSONDecodeError
from typing import Any, NoReturn

import httpx
import orjson
from pydantic import UUID4
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
from tracecat.executor.models import ExecutorActionErrorInfo, ExecutorSyncInput
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    RegistryActionValidateResponse,
)
from tracecat.types.auth import Role
from tracecat.types.exceptions import ExecutorClientError, RegistryError


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

    _timeout: float = config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT

    def __init__(self, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.logger = logger.bind(service="executor-client", role=self.role)

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[ExecutorHTTPClient]:
        async with ExecutorHTTPClient(self.role) as client:
            yield client

    # === Execution ===

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
            self._handle_http_status_error(e, action_type)
        except httpx.ReadTimeout as e:
            raise ExecutorClientError(
                f"Timeout calling action {action_type!r} in executor: {e}"
            ) from e
        except orjson.JSONDecodeError as e:
            raise ExecutorClientError(
                f"Error decoding JSON response for action {action_type!r}: {e}"
            ) from e
        except Exception as e:
            raise ExecutorClientError(
                f"Unexpected error calling action {action_type!r} in executor: {e}"
            ) from e

    # === Validation ===

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

    # === Management ===

    async def sync_executor(
        self, repository_id: UUID4, *, max_attempts: int = 3
    ) -> None:
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
                    response = await client.post(
                        "/sync",
                        content=ExecutorSyncInput(
                            repository_id=repository_id
                        ).model_dump_json(),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
            except Exception as e:
                logger.error("Error syncing executor", error=e)
                raise

        try:
            logger.info("Syncing executor", repository_id=repository_id)
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

    # === Utility ===

    def _handle_http_status_error(
        self, e: httpx.HTTPStatusError, action_type: str
    ) -> NoReturn:
        self.logger.info("Handling HTTP status error", error=e)
        try:
            resp = e.response.json()
        except JSONDecodeError:
            self.logger.warning("Failed to decode JSON response, returning empty dict")
            resp = {}
        if (
            isinstance(resp, Mapping)
            and (detail := resp.get("detail"))
            and isinstance(detail, Mapping)
        ):
            val_detail = ExecutorActionErrorInfo(**detail)
            detail = str(val_detail)
        else:
            detail = e.response.text
        self.logger.error("Executor returned an error", error=e, detail=detail)
        if e.response.status_code / 100 == 5:
            raise ExecutorClientError(
                f"There was an error in the executor when calling action {action_type!r} ({e.response.status_code}).\n\n{detail}"
            ) from e
        else:
            raise ExecutorClientError(
                f"Unexpected executor error ({e.response.status_code}):\n\n{e}\n\n{detail}"
            ) from e
