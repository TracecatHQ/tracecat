"""Tools exposed to the agent preset builder assistant.

This module re-exports the builder tool constants and bundled actions from the
internal tools module. The actual tool implementations are in
`tracecat/agent/mcp/internal_tools.py` and execute via the MCP trusted server.

For backwards compatibility, this module also exports the legacy pydantic-ai
tool builder function, but new code should use the MCP-based execution path.
"""

from __future__ import annotations

# Re-export constants from internal tools module
from tracecat.agent.mcp.internal_tools import (
    BUILDER_BUNDLED_ACTIONS,
    BUILDER_INTERNAL_TOOL_NAMES,
)

# Legacy constant name for backwards compatibility
# Maps to the internal tool names (without the internal.builder. prefix for display)
AGENT_PRESET_BUILDER_TOOL_NAMES = [
    name.replace("internal.builder.", "") for name in BUILDER_INTERNAL_TOOL_NAMES
]

__all__ = [
    "AGENT_PRESET_BUILDER_TOOL_NAMES",
    "BUILDER_BUNDLED_ACTIONS",
    "BUILDER_INTERNAL_TOOL_NAMES",
]
