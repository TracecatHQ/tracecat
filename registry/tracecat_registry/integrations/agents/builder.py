"""Pydantic AI agents with tool calling.

We use agent-wide dynamic tool preparation (deny-all by default):
https://ai.pydantic.dev/tools/#prepare-tools
"""

import inspect
import textwrap
from typing import Any, Union, Annotated
from typing_extensions import Doc
from timeit import timeit

from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.tools import Tool
from pydantic_core import PydanticUndefined, to_jsonable_python

from tracecat.dsl.common import create_default_execution_context
from tracecat.executor.service import _run_action_direct, run_template_action
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat_registry.integrations.pydantic_ai import (
    PYDANTIC_AI_REGISTRY_SECRETS,
    build_agent,
)

from tracecat_registry import registry
from tracecat.types.exceptions import RegistryError


def generate_google_style_docstring(description: str | None, model_cls: type) -> str:
    """Generate a Google-style docstring from a description and Pydantic model.

    Args:
        description: The base description for the function
        model_cls: The Pydantic model class containing parameter information

    Returns:
        A properly formatted Google-style docstring with Args section

    Raises:
        ValueError: If description is None
    """
    if description is None:
        raise ValueError("Tool description cannot be None")

    # Extract parameter descriptions from the model's JSON schema
    param_lines = []

    if hasattr(model_cls, "model_json_schema"):
        schema = model_cls.model_json_schema()
        properties = schema.get("properties", {})

        for prop_name, prop_info in properties.items():
            # Get description from schema, fall back to a placeholder if missing
            prop_desc = prop_info.get("description", f"Parameter {prop_name}")
            param_lines.append(f"{prop_name}: {prop_desc}")

    # Build the complete docstring
    if param_lines:
        params_section = "\n".join(param_lines)
        indented_params = textwrap.indent(params_section, "    ")
        return f"{description}\n\nArgs:\n{indented_params}"
    else:
        return f"{description}\n\nArgs:\n    None"


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


def _extract_action_metadata(bound_action) -> tuple[str, type]:
    """Extract description and model class from a bound action.

    Args:
        bound_action: The bound action from the registry

    Returns:
        Tuple of (description, model_cls)

    Raises:
        ValueError: If template action is not set or description is missing
    """
    if bound_action.is_template:
        if not bound_action.template_action:
            raise ValueError("Template action is not set")

        # Use template description with fallback
        description = (
            bound_action.template_action.definition.description
            or bound_action.description
        )

        # Get the model from expects
        expects = bound_action.template_action.definition.expects
        model_cls = create_expectation_model(
            expects, bound_action.template_action.definition.action.replace(".", "__")
        )
    else:
        # Use UDF description and args_cls
        description = bound_action.description
        model_cls = bound_action.args_cls

    return description, model_cls


def _create_function_signature(
    model_cls: type,
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Create function signature and annotations from a Pydantic model.

    Args:
        model_cls: The Pydantic model class

    Returns:
        Tuple of (signature, annotations)
    """
    sig_params = []
    annotations = {}

    for field_name, field_info in model_cls.model_fields.items():
        # Use the Pydantic field's annotation directly
        annotation = field_info.annotation

        # Handle defaults from Pydantic field
        if field_info.default is not PydanticUndefined:
            # Field has an explicit default value
            default = field_info.default
        elif field_info.default_factory is not None:
            # Field has a default factory
            default = None
            # Only wrap in Union if not already optional
            # Check if the annotation is already a Union type with None
            if hasattr(annotation, "__args__") and type(None) in annotation.__args__:
                # Already optional, don't double-wrap
                pass
            else:
                # Make optional types explicit for clarity
                annotation = Union[annotation, None]
        else:
            # Required field
            default = inspect.Parameter.empty

        # Create parameter
        param = inspect.Parameter(
            name=field_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=annotation,
            default=default,
        )
        sig_params.append(param)
        annotations[field_name] = annotation

    # Add return annotation
    annotations["return"] = Any

    return inspect.Signature(sig_params), annotations


def _generate_tool_function_name(namespace: str, name: str) -> str:
    """Generate a function name from namespace and action name.

    Args:
        namespace: The action namespace (e.g., "tools.slack")
        name: The action name (e.g., "post_message")

    Returns:
        Generated function name (e.g., "slack_post_message")
    """
    # Extract the last part of namespace after splitting by "."
    tool_name = namespace.split(".", maxsplit=1)[-1]
    return f"{tool_name}_{name}"


async def create_tool_from_registry(action_name: str) -> Tool:
    """Create a Pydantic AI Tool directly from the registry.

    Args:
        action_name: Full action name (e.g., "core.http_request")

    Returns:
        A configured Pydantic AI Tool

    Raises:
        ValueError: If action has no description or template action is invalid
    """
    # Load action from registry
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        bound_action = service.get_bound(reg_action, mode="execution")

    # Create wrapper function that calls the action
    async def tool_func(**kwargs) -> Any:
        return await call_tracecat_action(action_name, kwargs)

    # Set function name
    tool_func.__name__ = _generate_tool_function_name(
        bound_action.namespace, bound_action.name
    )

    # Extract metadata from the bound action
    description, model_cls = _extract_action_metadata(bound_action)

    # Validate description
    if not description:
        raise ValueError(f"Action '{action_name}' has no description")

    # Create function signature and annotations
    signature, annotations = _create_function_signature(model_cls)
    tool_func.__signature__ = signature
    tool_func.__annotations__ = annotations

    # Generate Google-style docstring
    tool_func.__doc__ = generate_google_style_docstring(description, model_cls)

    # Create tool with enforced documentation standards
    return Tool(
        tool_func, docstring_format="google", require_parameter_descriptions=True
    )


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

    def with_namespace_filters(self, namespaces: list[str]) -> "TracecatAgentBuilder":
        """Add a list of namespace filters for tools (e.g., ['tools.slack', 'tools.email'])."""
        self.namespace_filters.extend(namespaces)
        return self

    def with_action_filter(self, action_name: str) -> "TracecatAgentBuilder":
        """Add a specific action name filter (e.g., 'tools.slack.send_message')."""
        self.action_filters.append(action_name)
        return self

    def with_action_filters(self, action_names: list[str]) -> "TracecatAgentBuilder":
        """Add a list of action name filters (e.g., ['tools.slack.send_message', 'tools.email.send_email'])."""
        self.action_filters.extend(action_names)
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

        # Collect failed action names
        failed_actions: list[str] = []

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
            except RegistryError:
                failed_actions.append(action_name)

        # If there were failures, raise simple error
        if failed_actions:
            failed_list = "\n".join(f"- {action}" for action in failed_actions)
            raise ValueError(
                f"Unknown namespaces or action names. Please double check the following:\n{failed_list}"
            )

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


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
    namespace="ai",
)
async def agent(
    user_prompt: Annotated[str, Doc("User prompt to the agent.")],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    namespaces: Annotated[
        list[str], Doc("Namespaces (e.g. 'tools.slack') to include in the agent.")
    ],
    actions: Annotated[
        list[str],
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
    ],
    instructions: Annotated[str | None, Doc("Instructions for the agent.")] = None,
    include_usage: Annotated[
        bool, Doc("Whether to include usage information in the output.")
    ] = False,
    base_url: Annotated[str | None, Doc("Base URL of the model to use.")] = None,
) -> dict[str, str | dict[str, Any] | list[dict[str, Any]]]:
    builder = TracecatAgentBuilder(
        model_name=model_name,
        model_provider=model_provider,
        base_url=base_url,
        instructions=instructions,
    )
    agent = (
        await builder.with_namespace_filters(namespaces)
        .with_action_filters(actions)
        .build()
    )

    start_time = timeit()
    # Use async version since this function is already async
    result: AgentRunResult = await agent.run(user_prompt=user_prompt)
    end_time = timeit()

    output = result.output
    message_history = to_jsonable_python(result.all_messages())
    agent_output = {
        "output": output,
        "message_history": message_history,
        "duration": end_time - start_time,
    }
    if include_usage:
        agent_output["usage"] = to_jsonable_python(result.usage())

    return agent_output
