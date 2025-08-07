from abc import ABC, abstractmethod
from typing import Any

from tracecat_registry.integrations.agents.builder import create_tool_from_registry

from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService


def denormalize_tool_name(tool_name: str) -> str:
    """Convert a tool ID to a format that can be used to execute the tool."""
    return tool_name.replace("__", ".")


class ToolExecutor(ABC):
    """Client for tool calls."""

    @abstractmethod
    async def run(self, tool_name: str, args: dict[str, Any]) -> Any: ...


class SimpleToolExecutor(ToolExecutor):
    """Simple implementation of ToolExecutor that uses the registry to execute tools."""

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute a tool by ID with the given arguments.

        Args:
            tool_id: The tool identifier (e.g., "core.http.request" or "core__http__request")
            args: Dictionary of arguments to pass to the tool

        Returns:
            The result of the tool execution

        Raises:
            ModelRetry: If the tool requests a retry
            Exception: For any other tool execution errors
        """
        # Convert double underscores to dots for action name format
        logger.info("Executing tool call", tool_id=tool_name)

        # Create tool from registry
        async with RegistryActionsService.with_session() as svc:
            tool = await create_tool_from_registry(tool_name, args, service=svc)

        # Execute the tool function
        result = await tool.function(**args)  # type: ignore
        return result
