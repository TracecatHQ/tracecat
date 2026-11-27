"""Answer unexpected errors on secret-bearing URLs without re-raising them."""

from __future__ import annotations

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracecat.contexts import ctx_role
from tracecat.logger import logger
from tracecat.logger.redaction import redact_query_string, redact_url_secrets
from tracecat.observability.sentry import capture_api_exception


class URLSecretErrorMiddleware:
    """Keep the error text of secret-bearing requests out of logs and traces.

    Starlette's ``ServerErrorMiddleware`` answers an unhandled error with the
    500 handler, then re-raises it so the server logs the traceback and OTel
    records it on the span. For a URL that carries a secret the error text can
    contain it: a failed lookup echoes its bound parameters, the token among
    them. This middleware sits inside that layer and, for those URLs only,
    logs the error type, reports the error to Sentry (which drops exception
    text) and answers the same 500 body without re-raising.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        redacted_path = redact_url_secrets(path)
        if redacted_path == path and redact_query_string(query) == query:
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
            logger.error(
                "Unexpected error",
                error_type=type(exc).__name__,
                role=ctx_role.get(),
                path=redacted_path,
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
