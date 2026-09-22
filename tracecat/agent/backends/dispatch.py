"""Commit a prepared turn only after Temporal has built its start request."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from google.protobuf.message import Message
from temporalio.service import ServiceClient


class TurnDispatchClient(ServiceClient):
    """Request-scoped service client that commits immediately before start RPC.

    The regular Temporal client still owns validation, serialization, codecs,
    interceptors and request construction. The service boundary runs afterwards,
    allowing all preparation writes to roll back if any of those steps fail.
    """

    def __init__(
        self,
        wrapped: ServiceClient,
        commit: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(wrapped.config)
        self._wrapped = wrapped
        self._commit = commit
        self.committed = False

    @property
    def worker_service_client(self):
        """Expose the original client's worker connection."""
        return self._wrapped.worker_service_client

    def update_rpc_metadata(self, metadata: Mapping[str, str | bytes]) -> None:
        """Forward credential metadata updates to the underlying connection."""
        self._wrapped.update_rpc_metadata(metadata)

    def update_api_key(self, api_key: str | None) -> None:
        """Forward API key updates to the underlying connection."""
        self._wrapped.update_api_key(api_key)

    async def _rpc_call[ResponseT: Message](
        self,
        rpc: str,
        req: Message,
        resp_type: type[ResponseT],
        *,
        service: str,
        retry: bool,
        metadata: Mapping[str, str | bytes],
        timeout: timedelta | None,
    ) -> ResponseT:
        # This client belongs to one start attempt and must not send any other
        # operation while preparation holds the session lock.
        if service != "workflow" or rpc != "start_workflow_execution":
            raise RuntimeError("Turn dispatch only supports starting a workflow")
        if not self.committed:
            await self._commit()
            self.committed = True
        return await self._wrapped._rpc_call(
            rpc,
            req,
            resp_type,
            service=service,
            retry=retry,
            metadata=metadata,
            timeout=timeout,
        )
