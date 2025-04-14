from fastapi import Request, status
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    RemoveSearchAttributesRequest,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tracecat.config import TEMPORAL__CLUSTER_NAMESPACE
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatException


def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unexpected error",
        exc=exc,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"message": "An unexpected error occurred. Please try again later."},
    )


def bootstrap_role():
    """Role to bootstrap Tracecat services."""
    return Role(
        type="service",
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-bootstrap",
    )


def tracecat_exception_handler(request: Request, exc: TracecatException):
    """Generic exception handler for Tracecat exceptions.

    We can customize exceptions to expose only what should be user facing.
    """
    msg = str(exc)
    logger.error(
        msg,
        role=ctx_role.get(),
        params=request.query_params,
        path=request.url.path,
        detail=exc.detail,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"type": type(exc).__name__, "message": msg, "detail": exc.detail},
    )


def custom_generate_unique_id(route: APIRoute):
    if route.tags:
        return f"{route.tags[0]}-{route.name}"
    return route.name


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=5, min=5, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def add_temporal_search_attributes():
    """Add search attributes to the Temporal cluster.

    This is an idempotent operation and is safe to run multiple times.
    """
    client = await get_temporal_client()
    namespace = TEMPORAL__CLUSTER_NAMESPACE
    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                search_attributes={
                    "TracecatTriggerType": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "TracecatTriggeredByUserId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                },
                namespace=namespace,
            )
        )
    except Exception as e:
        logger.error(
            "Error adding temporal search attributes",
            exc=e,
            namespace=namespace,
        )
    else:
        logger.info(
            "Temporal search attributes added",
            namespace=namespace,
            search_attributes=["TracecatTriggerType", "TracecatTriggeredByUserId"],
        )


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=5, min=5, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def remove_temporal_search_attributes():
    """Remove search attributes from the Temporal cluster.

    This is an idempotent operation and is safe to run multiple times.
    """
    client = await get_temporal_client()
    namespace = TEMPORAL__CLUSTER_NAMESPACE
    try:
        await client.operator_service.remove_search_attributes(
            RemoveSearchAttributesRequest(
                search_attributes=[
                    "TracecatTriggerType",
                    "TracecatTriggeredByUserId",
                ],
                namespace=namespace,
            )
        )
    except Exception as e:
        logger.error(
            "Error removing temporal search attributes",
            exc=e,
            namespace=namespace,
        )
    else:
        logger.info(
            "Temporal search attributes removed",
            namespace=namespace,
            search_attributes=["TracecatTriggerType", "TracecatTriggeredByUserId"],
        )
