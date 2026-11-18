"""Cancel selected requests when their client disconnects."""

from __future__ import annotations

import asyncio
from collections.abc import Collection

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CancelOnDisconnectMiddleware:
    """Cancel a request's handler once its client disconnects.

    The case list aborts a search when the user types the next character, but
    the server kept running the abandoned statements until they finished or
    hit their timeout, holding a database connection the whole time.

    This is plain ASGI on purpose. ``request.is_disconnected()`` cannot see a
    disconnect behind ``BaseHTTPMiddleware``: each layer reads ``receive``
    inside a task group, and the cancelled read that ``is_disconnected`` makes
    discards the message on the way back. Here the middleware reads the whole
    body itself, replays it to the app, and watches the server's own
    ``receive`` for ``http.disconnect``. Cancelling the handler alone does not
    stop its SQL; the routes wrap their work in
    ``cancel_statement_on_cancellation``, which cancels the running statement
    in PostgreSQL before the cancellation unwinds.

    A disconnect after the response is complete is the normal end of a request
    and cancels nothing, so after-response work such as logging still runs.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        path_suffixes: Collection[str],
        methods: Collection[str] = frozenset({"POST"}),
    ) -> None:
        self.app = app
        self._path_suffixes = tuple(path_suffixes)
        self._methods = frozenset(methods)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in self._methods
            or not scope["path"].endswith(self._path_suffixes)
        ):
            await self.app(scope, receive, send)
            return

        # Own receive from here on: read the body, then only a disconnect can
        # arrive, which the watcher below waits for.
        body: list[Message] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.append(message)
            if not message.get("more_body", False):
                break

        client_gone = asyncio.Event()
        response_done = False

        async def replay() -> Message:
            if body:
                return body.pop(0)
            await client_gone.wait()
            return {"type": "http.disconnect"}

        async def tracked_send(message: Message) -> None:
            nonlocal response_done
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                response_done = True
            await send(message)

        handler = asyncio.ensure_future(self.app(scope, replay, tracked_send))

        async def watch() -> None:
            message = await receive()
            if message["type"] == "http.disconnect" and not response_done:
                client_gone.set()
                handler.cancel()

        watcher = asyncio.ensure_future(watch())
        try:
            await handler
        except asyncio.CancelledError:
            if not client_gone.is_set():
                raise
            # The client left; there is no one to send a response to.
        finally:
            watcher.cancel()
            if not handler.done():
                # This middleware itself was cancelled.
                handler.cancel()
                await asyncio.gather(handler, return_exceptions=True)
