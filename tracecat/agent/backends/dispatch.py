"""Commit a prepared turn only after Temporal has built its start request."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

from google.protobuf.message import Message
from temporalio.service import RPCError, RPCStatusCode, ServiceClient

_REJECTED_START_STATUSES = frozenset(
    {
        RPCStatusCode.INVALID_ARGUMENT,
        RPCStatusCode.NOT_FOUND,
        RPCStatusCode.PERMISSION_DENIED,
        RPCStatusCode.FAILED_PRECONDITION,
        RPCStatusCode.OUT_OF_RANGE,
        RPCStatusCode.UNIMPLEMENTED,
        RPCStatusCode.UNAUTHENTICATED,
    }
)


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
        self.commit_attempted = False
        self.committed = False
        self.rejected = False

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
        if self.commit_attempted:
            raise RuntimeError("Turn dispatch only supports one start attempt")
        self.commit_attempted = True
        await self._commit()
        self.committed = True
        try:
            return await self._wrapped._rpc_call(
                rpc,
                req,
                resp_type,
                service=service,
                # A final rejection after an SDK retry cannot rule out an earlier
                # accepted start whose acknowledgement was lost. Observe exactly
                # one attempt so only definitive rejections release ownership.
                retry=False,
                metadata=metadata,
                timeout=timeout,
            )
        except RPCError as exc:
            self.rejected = exc.status in _REJECTED_START_STATUSES
            raise
