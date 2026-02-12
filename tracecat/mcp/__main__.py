"""Entry point for the Tracecat MCP server.

Usage:
    python -m tracecat.mcp
"""

from tracecat.mcp.config import TRACECAT_MCP__HOST, TRACECAT_MCP__PORT
from tracecat.mcp.server import mcp

mcp.run(transport="streamable-http", host=TRACECAT_MCP__HOST, port=TRACECAT_MCP__PORT)
