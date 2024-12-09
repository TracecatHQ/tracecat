from tracecat.dsl.models import RunActionInput
from tracecat.ee.store.models import ActionRefHandle
from tracecat.executor.client import ExecutorClient, ExecutorHTTPClient
from tracecat.logger import logger


class ExecutorClientEE(ExecutorClient):
    """EE version of the executor client"""

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
