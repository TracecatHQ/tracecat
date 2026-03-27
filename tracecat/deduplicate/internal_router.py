"""Internal router for workspace-scoped deduplication.

Provides a trusted-side API for persistent deduplication so that sandboxed
registry actions can check-and-set dedup state without direct Redis access.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client


class CheckAndSetRequest(BaseModel):
    """Batch check-and-set request for deduplication digests."""

    digests: list[str] = Field(min_length=1, max_length=1000)
    expire_seconds: int = Field(ge=1, le=2592000)


class CheckAndSetResponse(BaseModel):
    """Response indicating which digests were newly inserted."""

    inserted: list[bool]


router = APIRouter(
    prefix="/internal/deduplicate",
    tags=["internal-deduplicate"],
    include_in_schema=False,
)


@router.post("/check-and-set")
@require_scope("action:*:execute")
async def check_and_set(
    *,
    role: ExecutorWorkspaceRole,
    request: CheckAndSetRequest,
) -> CheckAndSetResponse:
    """Atomically check and set deduplication digests.

    For each digest, sets a Redis key with NX (set-if-not-exists) semantics.
    Returns a list of booleans indicating which digests were newly inserted.
    Workspace scope is derived from the executor token, never from the request.

    Args:
        role: Executor workspace role (provides workspace_id).
        request: Batch of digests and TTL.

    Returns:
        CheckAndSetResponse with inserted flags aligned to input order.
    """
    workspace_id = role.workspace_id
    redis_client = await get_redis_client()

    try:
        if len(request.digests) > 10:
            # Pipeline path: reduce round-trips for larger batches
            raw_client = await redis_client._get_client()
            pipe = raw_client.pipeline(transaction=False)
            for digest in request.digests:
                key = f"dedup:{workspace_id}:{digest}"
                pipe.set(key, "1", ex=request.expire_seconds, nx=True)
            results = await pipe.execute()
            inserted = [bool(r) for r in results]
        else:
            # Sequential path: negligible RTT for small batches
            inserted = []
            for digest in request.digests:
                key = f"dedup:{workspace_id}:{digest}"
                was_set = await redis_client.set_if_not_exists(
                    key, "1", expire_seconds=request.expire_seconds
                )
                inserted.append(was_set)
    except RedisError as e:
        logger.error(
            "Deduplication check-and-set failed",
            workspace_id=str(workspace_id),
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Deduplication service temporarily unavailable",
        ) from e

    return CheckAndSetResponse(inserted=inserted)
