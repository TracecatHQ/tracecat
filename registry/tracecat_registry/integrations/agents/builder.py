"""Pydantic AI agents with tool calling.

We use agent-wide dynamic tool preparation (deny-all by default):
https://ai.pydantic.dev/tools/#prepare-tools
"""

import inspect
from typing import Any, Union

from pydantic_ai import Agent
from pydantic_ai.tools import Tool

from tracecat.dsl.common import create_default_execution_context
from tracecat.executor.service import _run_action_direct, run_template_action
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat_registry.integrations.pydantic_ai import build_agent


async def call_tracecat_action(action_name: str, args: dict[str, Any]) -> Any:
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        bound_action = service.get_bound(reg_action, mode="execution")

    # Call directly based on action type
    if bound_action.is_template:
        # For templates, create a minimal execution context
        # Secrets are already in the environment, so we don't need to pass them
        context = create_default_execution_context()

        result = await run_template_action(
            action=bound_action,
            args=args,
            context=context,
        )
    else:
        # UDFs can be called directly - secrets are already in the environment
        result = await _run_action_direct(
            action=bound_action,
            args=args,
        )
    return result


async def create_tool_from_registry(action_name: str) -> Tool:
    """
    Create a Pydantic AI Tool directly from the registry.

    This properly extracts:
    - Function docstring as tool description
    - Parameter types and descriptions from Pydantic models
    - Default values and optional parameters
    """

    # Load action from registry to get signature
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        bound_action = service.get_bound(reg_action, mode="execution")

    # Create the tool function
    async def tool_func(**kwargs) -> Any:
        return await call_tracecat_action(action_name, kwargs)

    # Set function metadata for PydanticAI
    tool_name = bound_action.namespace.split(".", maxsplit=1)[-1]
    func_name = bound_action.name
    tool_func.__name__ = f"{tool_name}_{func_name}"

    # Extract description - PydanticAI uses this as the tool description
    if bound_action.is_template and bound_action.template_action:
        # For templates, use the template description
        tool_func.__doc__ = (
            bound_action.template_action.definition.description
            or bound_action.description
        )
    else:
        # For UDFs, use the action description
        tool_func.__doc__ = bound_action.description

    # Extract function signature with proper type annotations
    if bound_action.is_template:
        if not bound_action.template_action:
            raise ValueError("Template action is not set")

        # For templates, use the expects field to create the signature
        expects = bound_action.template_action.definition.expects
        temp_model = create_expectation_model(
            expects, bound_action.template_action.definition.action.replace(".", "__")
        )
        model_fields = temp_model.model_fields
    else:
        # For UDFs, use the args_cls directly
        model_fields = bound_action.args_cls.model_fields

    # Convert to function signature with proper annotations
    sig_params = []
    for field_name, field_info in model_fields.items():
        annotation = field_info.annotation

        # Handle default values
        if field_info.default is not ...:
            default = field_info.default
        elif field_info.default_factory is not None:
            default = None
            # Make the annotation optional if it has a default factory
            annotation = Union[annotation, None]
        else:
            default = inspect.Parameter.empty

        param = inspect.Parameter(
            name=field_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=annotation,
            default=default,
        )
        sig_params.append(param)

    # Set the function signature - PydanticAI will extract schema from this
    tool_func.__signature__ = inspect.Signature(sig_params)

    # Also set __annotations__ for PydanticAI type hint extraction
    annotations = {}
    for field_name, field_info in model_fields.items():
        annotation = field_info.annotation
        # Handle default factory case for annotations
        if field_info.default_factory is not None:
            annotation = Union[annotation, None]
        annotations[field_name] = annotation

    # Set return type annotation
    annotations["return"] = Any
    tool_func.__annotations__ = annotations

    # Create the Tool - PydanticAI will automatically extract:
    # - Tool name from function name
    # - Tool description from function docstring
    # - Parameter schema from function signature and annotations
    return Tool(tool_func)


class TracecatAgentBuilder:
    """Builder for creating Pydantic AI agents with Tracecat tool calling."""

    def __init__(
        self,
        model_name: str,
        model_provider: str,
        base_url: str | None = None,
        instructions: str | None = None,
        output_type: str | dict[str, Any] | None = None,
        model_settings: dict[str, Any] | None = None,
        retries: int = 3,
        deps_type: type[Any] | None = None,
    ):
        self.model_name = model_name
        self.model_provider = model_provider
        self.base_url = base_url
        self.instructions = instructions
        self.output_type = output_type
        self.model_settings = model_settings
        self.retries = retries
        self.deps_type = deps_type
        self.tools: list[Tool] = []
        self.namespace_filters: list[str] = []
        self.action_filters: list[str] = []

    def with_namespace_filter(self, namespace: str) -> "TracecatAgentBuilder":
        """Add a namespace filter for tools (e.g., 'tools.slack')."""
        self.namespace_filters.append(namespace)
        return self

    def with_action_filter(self, action_name: str) -> "TracecatAgentBuilder":
        """Add a specific action name filter (e.g., 'tools.slack.send_message')."""
        self.action_filters.append(action_name)
        return self

    def with_custom_tool(self, tool: Tool) -> "TracecatAgentBuilder":
        """Add a custom Pydantic AI tool."""
        self.tools.append(tool)
        return self

    async def build(self) -> Agent:
        """Build the Pydantic AI agent with tools from the registry."""

        # Get actions from registry
        async with RegistryActionsService.with_session() as service:
            if self.action_filters:
                actions = await service.get_actions(self.action_filters)
            else:
                actions = await service.list_actions(include_marked=True)

        # Create tools from registry actions
        for reg_action in actions:
            action_name = f"{reg_action.namespace}.{reg_action.name}"

            # Apply namespace filtering if specified
            if self.namespace_filters:
                if not any(action_name.startswith(ns) for ns in self.namespace_filters):
                    continue

            try:
                tool = await create_tool_from_registry(action_name)
                self.tools.append(tool)
            except Exception as e:
                raise ValueError(f"Failed to create tool from registry: {e}") from e

        # Create the agent using build_agent
        agent = build_agent(
            model_name=self.model_name,
            model_provider=self.model_provider,
            base_url=self.base_url,
            instructions=self.instructions,
            output_type=self.output_type,
            model_settings=self.model_settings,
            retries=self.retries,
            deps_type=self.deps_type,
            tools=self.tools,
        )

        logger.info(
            "Built Tracecat agent",
            model=self.model_name,
            provider=self.model_provider,
            tool_count=len(self.tools),
        )
        return agent
