from fastapi import Request, status
from fastapi.responses import ORJSONResponse
from fastapi.routing import APIRoute

from tracecat.contexts import ctx_role
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
