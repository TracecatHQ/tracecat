"""Entry point for the Tracecat MCP server.

Usage:
    python -m tracecat.mcp
"""

from __future__ import annotations

import importlib
import sys
import time

from tracecat.logger import logger
from tracecat.mcp.config import (
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
    module.mcp.run(
        transport="streamable-http", host=TRACECAT_MCP__HOST, port=TRACECAT_MCP__PORT
    )


def main() -> None:
    """Start the MCP server with bounded startup retries."""
    max_attempts = max(TRACECAT_MCP__STARTUP_MAX_ATTEMPTS, 1)
    for attempt in range(1, max_attempts + 1):
        try:
            _run_mcp_server()
            return
        except KeyboardInterrupt:
            logger.info("MCP server interrupted; shutting down")
            return
        except Exception:
            should_retry = attempt < max_attempts
            if not should_retry:
                logger.exception(
                    "MCP server failed to start after maximum startup attempts",
                    attempts=max_attempts,
                )
                raise SystemExit(1) from None
            logger.exception(
                "MCP server startup failed; retrying",
                attempt=attempt,
                max_attempts=max_attempts,
                retry_delay_seconds=TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS,
            )
            time.sleep(max(TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS, 0.0))


if __name__ == "__main__":
    main()
