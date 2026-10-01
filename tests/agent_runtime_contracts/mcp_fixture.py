"""Synthetic, credential-free MCP catalog for testing the installed Claude CLI."""

import sys
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.base import Tool, ToolResult


class FixtureTool(Tool):
    """Accept the runtime's injected metadata at the MCP JSON boundary."""

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        if self.name == "required_lookup":
            return ToolResult(content=f"fixture-device:{arguments['device_id']}")
        return ToolResult(content=f"unrelated:{arguments['query']}")


def make_server(*, always_load: bool = True) -> FastMCP:
    """Provide one selected tool and one unrelated tool with full schemas."""
    server = FastMCP("readiness-fixture")
    for name, param, meta in (
        (
            "required_lookup",
            "device_id",
            {"anthropic/alwaysLoad": True} if always_load else None,
        ),
        ("unrelated_lookup", "query", None),
    ):
        server.add_tool(
            FixtureTool(
                name=name,
                description=f"Synthetic {name}",
                parameters={
                    "type": "object",
                    "properties": {param: {"type": "string"}},
                    "required": [param],
                },
                meta=meta,
            )
        )
    return server


if __name__ == "__main__":
    make_server(always_load="--deferred" not in sys.argv).run(
        transport="stdio", show_banner=False
    )
