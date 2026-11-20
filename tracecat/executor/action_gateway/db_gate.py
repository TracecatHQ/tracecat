"""Per-org DB session cap for Action Gateway operations."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request, status
from fastapi.responses import ORJSONResponse

from tracecat.auth.credentials import _get_workspace_org_id, get_bearer_token
from tracecat.auth.executor_tokens import verify_executor_token
from tracecat.db.exceptions import TenantBusyError
from tracecat.db.org_gate import get_org_session_gate
from tracecat.executor.action_gateway.policy import ACTION_GATEWAY_HEALTH_PATH

TENANT_BUSY_ERROR_CODE = "tenant_db_busy"
"""Error code of a pre-effect capacity refusal. The registry SDK retries only
this code (``tracecat_registry.sdk.client``)."""


async def hold_org_db_session_unit(request: Request) -> AsyncGenerator[None]:
    """Hold one of the org's DB session units for the whole operation.

    Runs as an app-level dependency, before route authentication opens the
    request session, so an org over its cap waits without holding a
    connection. The org comes from the executor token's workspace (a cached,
    immutable lookup). Requests without a valid token pass through: route
    authentication rejects them.

    Raises:
        TenantBusyError: The org stayed at its cap; nothing has run yet.
    """
    gate = get_org_session_gate()
    if gate is None or request.url.path == ACTION_GATEWAY_HEALTH_PATH:
        yield
        return
    if (token := get_bearer_token(request)) is None:
        yield
        return
    try:
        claims = verify_executor_token(token)
    except ValueError:
        yield
        return
    if (organization_id := await _get_workspace_org_id(claims.workspace_id)) is None:
        yield
        return
    async with gate.hold(organization_id):
        yield


def tenant_busy_exception_handler(_request: Request, exc: Exception) -> ORJSONResponse:
    """Map a tenant-busy refusal to a retryable 503 with ``Retry-After``.

    The SDK retries this code, which is safe only because the gate dependency
    is the sole raiser and refuses before the route runs. If session factories
    are gated later, a refusal raised mid-operation must map to a different,
    non-retryable error.
    """
    retry_after = exc.retry_after_seconds if isinstance(exc, TenantBusyError) else 1
    return ORJSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": str(retry_after)},
        content={
            "detail": {
                "code": TENANT_BUSY_ERROR_CODE,
                "message": (
                    "This organization is at its database capacity in this "
                    "executor. The request did not run; retry it."
                ),
                "retry_after_seconds": retry_after,
            }
        },
    )
