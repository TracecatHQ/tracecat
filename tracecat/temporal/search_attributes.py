"""Temporal search-attribute readiness checks for workflow workers."""

from datetime import timedelta

from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from tracecat.logger import logger
from tracecat.workflow.executions.enums import TemporalSearchAttr

_ERROR_OWNER_PROBE_QUERY = f"{TemporalSearchAttr.ERROR_OWNER.value} IS NULL"
_SEARCH_ATTRIBUTE_PROBE_TIMEOUT = timedelta(seconds=10)


async def ensure_error_owner_search_attribute(client: Client) -> None:
    """Fail worker startup unless terminal error attribution is queryable.

    Runtime Temporal Cloud credentials are namespace-admin credentials and may
    call WorkflowService but not OperatorService, so type registration remains
    an administrative provisioning invariant. This visibility probe must run
    before a worker polls: a missing attribute would otherwise reject the
    terminal upsert and retry the workflow task indefinitely.
    """
    attribute = TemporalSearchAttr.ERROR_OWNER.value
    try:
        await client.count_workflows(
            _ERROR_OWNER_PROBE_QUERY,
            rpc_timeout=_SEARCH_ATTRIBUTE_PROBE_TIMEOUT,
        )
    except RPCError as error:
        if error.status is not RPCStatusCode.INVALID_ARGUMENT:
            raise
        raise RuntimeError(
            "Temporal is not ready for runtime error attribution: required "
            f"Keyword search attribute {attribute!r} is missing or "
            "inaccessible. Provision it before starting Tracecat workers."
        ) from error

    logger.info(
        "Verified Temporal runtime error attribution search attribute",
        search_attribute=attribute,
    )
