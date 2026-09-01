"""Temporal search-attribute readiness checks for workflow workers."""

from datetime import timedelta

from temporalio.client import Client
from temporalio.service import RPCError

from tracecat.logger import logger
from tracecat.workflow.executions.enums import TemporalSearchAttr

_ERROR_OWNER_PROBE_QUERY = f"{TemporalSearchAttr.ERROR_OWNER.value} IS NULL"
_SEARCH_ATTRIBUTE_PROBE_TIMEOUT = timedelta(seconds=10)


async def ensure_error_owner_search_attribute(client: Client) -> None:
    """Fail worker startup unless terminal error attribution is queryable.

    The Visibility API validates custom search attributes without requiring the
    OperatorService permissions that runtime Temporal Cloud credentials may not
    have. This must run before a worker polls: an invalid workflow upsert is
    rejected after workflow code returns and would otherwise retry the workflow
    task indefinitely instead of recording its terminal failure.
    """
    try:
        await client.count_workflows(
            _ERROR_OWNER_PROBE_QUERY,
            rpc_timeout=_SEARCH_ATTRIBUTE_PROBE_TIMEOUT,
        )
    except RPCError as error:
        raise RuntimeError(
            "Temporal is not ready for runtime error attribution: required "
            "Keyword search attribute 'TracecatErrorOwner' is missing or "
            "inaccessible. Provision it before starting Tracecat workers."
        ) from error

    logger.info(
        "Verified Temporal runtime error attribution search attribute",
        search_attribute=TemporalSearchAttr.ERROR_OWNER.value,
    )
