"""Answer unexpected errors on secret-bearing URLs without re-raising them."""

from __future__ import annotations

import traceback

from fastapi import status
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.logger.redaction import SECRET_QUERY_KEYS, redact_url_secrets
from tracecat.observability.sentry import capture_api_exception


class URLSecretErrorMiddleware:
    """Keep the error text of secret-bearing requests out of logs and traces.

    Starlette's ``ServerErrorMiddleware`` answers an unhandled error with the
    500 handler, then re-raises it so the server logs the traceback and OTel
    records it on the span. For a URL that carries a secret the error text can
    contain it: a failed lookup echoes its bound parameters, the token among
    them. This middleware sits inside that layer. For routes with a secret in
    the path or a credential query parameter, it logs the error type and stack
    frames, reports the error to Sentry (which drops exception text) and
    answers the same 500 body without re-raising. Other errors pass through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracking_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking_start)
        except Exception as exc:
            if not _carries_url_secret(scope):
                raise
            logger.error(
                "Unexpected error",
                error_type=type(exc).__name__,
                # Frames only (file, line, function, source): the error text
                # is left out because it can echo the secret.
                frames="".join(traceback.format_tb(exc.__traceback__)),
                role=ctx_role.get(),
                path=redact_url_secrets(scope["path"]),
            )
            capture_api_exception(exc)
            if response_started:
                return
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "message": "An unexpected error occurred. Please try again later."
                },
            )
            await response(scope, receive, send)


def _carries_url_secret(scope: Scope) -> bool:
    """Whether the request's route puts a secret in its URL.

    Decided from the path, which the route fixes, and from the query
    parameters the matched route declares, not from the raw query, which any
    client can fill with a credential-shaped name.
    """
    path: str = scope["path"]
    if redact_url_secrets(path) != path:
        return True
    route = scope.get("route")
    if not isinstance(route, APIRoute):
        return False
    declared = {
        param.alias for param in get_flat_dependant(route.dependant).query_params
    }
    return not declared.isdisjoint(SECRET_QUERY_KEYS)
