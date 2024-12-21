import httpx
import orjson

from tracecat.dsl.models import RunActionInput
from tracecat.ee.store.models import ActionResultHandle
from tracecat.executor.client import ExecutorClient
from tracecat.logger import logger
from tracecat.types.exceptions import ActionExecutionError


class ExecutorClientEE(ExecutorClient):
    """EE version of the executor client"""

    async def run_action_store_backend(
        self, input: RunActionInput
    ) -> ActionResultHandle:
        action_type = input.task.action
        content = input.model_dump_json()
        logger.debug(
            f"Calling action {action_type!r} in store mode with content",
            content=content,
            role=self.role,
            timeout=self._timeout,
        )
        try:
            async with self._client() as client:
                # No need to include role headers here because it's already
                # added in AuthenticatedServiceClient
                response = await client.post(
                    f"/run-store/{action_type}",
                    headers={"Content-Type": "application/json"},
                    content=content,
                    timeout=self._timeout,
                )
            response.raise_for_status()
            return ActionResultHandle.model_validate_json(response.content)
        except httpx.HTTPStatusError as e:
            self._handle_http_status_error(e, action_type)
        except httpx.ReadTimeout as e:
            raise ActionExecutionError(
                f"Timeout calling action {action_type!r} in executor: {e}"
            ) from e
        except orjson.JSONDecodeError as e:
            raise ActionExecutionError(
                f"Error decoding JSON response for action {action_type!r}: {e}"
            ) from e
        except Exception as e:
            raise ActionExecutionError(
                f"Unexpected error calling action {action_type!r} in executor: {e}"
            ) from e
