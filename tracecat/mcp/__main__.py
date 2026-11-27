"""Entry point for the Tracecat MCP server.

Usage:
    python -m tracecat.mcp
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import time

from sse_starlette.sse import AppStatus

from tracecat.db.engine import verify_rls_enforceable_login
from tracecat.db.roles import UnsafeDatabaseLoginError
from tracecat.logger import logger
from tracecat.logger.redaction import install_uvicorn_url_secret_redaction
from tracecat.mcp.config import (
    TRACECAT_MCP__GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
    TRACECAT_MCP__HOST,
    TRACECAT_MCP__PORT,
    TRACECAT_MCP__STARTUP_MAX_ATTEMPTS,
    TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS,
)


def _run_mcp_server() -> None:
    """Import and run the MCP server."""
    server_module_name = "tracecat.mcp.server"
    if server_module_name in sys.modules:
        module = importlib.reload(sys.modules[server_module_name])
    else:
        module = importlib.import_module(server_module_name)
    # sse-starlette cancels every open SSE response as soon as uvicorn receives
    # SIGTERM, which drops in-flight tool results. Let uvicorn's graceful
    # shutdown timeout bound the drain instead.
    AppStatus.disable_automatic_graceful_drain()
    # The OAuth callback carries the authorization code and state in its query.
    install_uvicorn_url_secret_redaction()
    module.mcp.run(
        transport="streamable-http",
        host=TRACECAT_MCP__HOST,
        port=TRACECAT_MCP__PORT,
        stateless_http=True,
        # FastMCP defaults this to 2s, which cancels in-flight tool calls.
        uvicorn_config={
            "timeout_graceful_shutdown": TRACECAT_MCP__GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
        },
    )


def main() -> None:
    """Start the MCP server with bounded startup retries."""
    max_attempts = max(TRACECAT_MCP__STARTUP_MAX_ATTEMPTS, 1)
    for attempt in range(1, max_attempts + 1):
        try:
            asyncio.run(verify_rls_enforceable_login())
            _run_mcp_server()
            return
        except UnsafeDatabaseLoginError as e:
            # Misconfiguration, not a transient failure: retrying can't fix it.
            logger.error("MCP server refused an unsafe database login", error=str(e))
            raise SystemExit(1) from None
        except KeyboardInterrupt:
            logger.info("MCP server interrupted; shutting down")
            return
        except Exception as e:
            should_retry = attempt < max_attempts
            error = str(e)
            if not should_retry:
                logger.error(
                    "MCP server failed to start after maximum startup attempts",
                    attempts=max_attempts,
                    error=error,
                )
                raise SystemExit(1) from None
            logger.warning(
                "MCP server startup failed; retrying",
                attempt=attempt,
                max_attempts=max_attempts,
                retry_delay_seconds=TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS,
                error=error,
            )
            time.sleep(max(TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS, 0.0))


if __name__ == "__main__":
    main()
