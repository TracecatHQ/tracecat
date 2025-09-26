"""Functions that create and call tools added to the agent."""

import inspect
import keyword
import textwrap
from dataclasses import dataclass
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai.tools import Tool
from pydantic_core import PydanticUndefined
from tracecat_registry import RegistrySecretType

from tracecat.config import TRACECAT__AGENT_MAX_TOOLS
from tracecat.db.schemas import RegistryAction
from tracecat.dsl.common import create_default_execution_context
from tracecat.executor.service import (
    _run_action_direct,
    flatten_secrets,
    get_action_secrets,
    run_template_action,
)
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.secrets_manager import env_sandbox


async def call_tracecat_action(
    action_name: str,
    args: dict[str, Any],
    *,
    service: RegistryActionsService | None = None,
) -> Any:
    # Use provided service or create a new session context
    if service is None:
        async with RegistryActionsService.with_session() as session_service:
            return await call_tracecat_action(
                action_name, args, service=session_service
            )

    # Service provided - use it directly
    reg_action = await service.get_action(action_name)
    action_secrets = await service.fetch_all_action_secrets(reg_action)
    bound_action = service.get_bound(reg_action, mode="execution")

    secrets = await get_action_secrets(args=args, action_secrets=action_secrets)

    # Call action with secrets in environment
    context = create_default_execution_context()
    context.update(SECRETS=secrets)  # type: ignore

    flattened_secrets = flatten_secrets(secrets)
    try:
        with env_sandbox(flattened_secrets):
            # Call directly based on action type
            if bound_action.is_template:
                # For templates, pass the context with secrets
                result = await run_template_action(
                    action=bound_action,
                    args=args,
                    context=context,
                )
            else:
                # UDFs can be called directly - secrets are now in the environment
                result = await _run_action_direct(
                    action=bound_action,
                    args=args,
                )
    except Exception as e:
        raise ModelRetry(str(e)) from e
    return result


async def create_tool_from_registry(
    action_name: str,
    fixed_args: dict[str, Any] | None = None,
    *,
    service: RegistryActionsService | None = None,
) -> Tool:
    """Create a Pydantic AI Tool directly from the registry.

    Args:
        action_name: Full action name (e.g., "core.http_request")
        fixed_args: Fixed arguments to curry into the tool function

    Returns:
        A configured Pydantic AI Tool

    Raises:
        ValueError: If action has no description or template action is invalid
    """
    # Load action from registry
    if service is None:
        async with RegistryActionsService.with_session() as _service:
            return await create_tool_from_registry(
                action_name, fixed_args, service=_service
            )

    reg_action = await service.get_action(action_name)
    bound_action = service.get_bound(reg_action, mode="execution")

    fixed_args = fixed_args or {}
    fixed_arg_names = set(fixed_args.keys())

    # Extract metadata from the bound action
    description, model_cls = _extract_action_metadata(bound_action)

    # Create function signature and get parameter mapping
    sig = _create_function_signature(model_cls, fixed_arg_names)

    # Create wrapper function that calls the action with fixed args merged
    async def tool_func(**kwargs: Any) -> Any:
        # Remap sanitized parameter names back to original field names
        remapped_kwargs = {}
        for param_name, value in kwargs.items():
            original_name = sig.param_mapping.get(param_name, param_name)
            remapped_kwargs[original_name] = value

        # Merge fixed arguments with runtime arguments
        merged_args = {**fixed_args, **remapped_kwargs}
        # Important: Do not reuse the builder's service/session for execution.
        # Each tool invocation should create its own short-lived DB session to
        # avoid concurrent operations on a shared AsyncSession when multiple
        # tools are called in parallel by the agent.
        return await call_tracecat_action(action_name, merged_args)

    # Set function name
    tool_func.__name__ = _generate_tool_function_name(
        bound_action.namespace, bound_action.name
    )

    # Validate description
    if not description:
        raise ValueError(f"Action '{action_name}' has no description")

    # Set function signature and annotations
    tool_func.__signature__ = sig.signature
    tool_func.__annotations__ = sig.annotations

    # Generate Google-style docstring, excluding fixed args
    tool_func.__doc__ = _generate_google_style_docstring(
        description, model_cls, fixed_arg_names
    )

    # Create tool with enforced documentation standards
    return Tool(
        tool_func, docstring_format="google", require_parameter_descriptions=False
    )


@dataclass
class CreateToolResult:
    """Result of creating a single tool from a registry action."""

    tool: Tool
    """The created tool, or None if creation failed."""
    collected_secrets: set[RegistrySecretType]
    """Secrets collected during tool creation."""
    action_name: str
    """The action name that was processed."""


async def create_single_tool(
    service: RegistryActionsService,
    ra: RegistryAction,
    action_name: str,
    fixed_arguments: dict[str, dict[str, Any]] | None = None,
) -> CreateToolResult | None:
    """Create a single tool from a registry action.

    Args:
        service: The registry actions service instance
        ra: The registry action to create a tool from
        action_name: The formatted action name (namespace.name)
        fixed_arguments: Fixed arguments for actions

    Returns:
        CreateToolResult containing the tool and metadata, or None if creation failed
    """
    collected_secrets: set[RegistrySecretType] = set()
    fixed_arguments = fixed_arguments or {}

    try:
        # Fetch all secrets for this action
        action_secrets = await service.fetch_all_action_secrets(ra)
        collected_secrets.update(action_secrets)

        # Get fixed arguments for this specific action
        action_fixed_args = fixed_arguments.get(action_name)
        tool = await create_tool_from_registry(
            action_name, action_fixed_args, service=service
        )

        return CreateToolResult(
            tool=tool,
            collected_secrets=collected_secrets,
            action_name=action_name,
        )
    except Exception as e:
        logger.error(
            "Failed to create tool from registry action",
            action_name=action_name,
            error=str(e),
        )
        return None


@dataclass
class BuildToolsResult[DepsT]:
    tools: list[Tool[DepsT]]
    collected_secrets: set[RegistrySecretType]


async def build_agent_tools(
    namespaces: list[str] | None = None,
    actions: list[str] | None = None,
    fixed_arguments: dict[str, dict[str, Any]] | None = None,
    max_tools: int = TRACECAT__AGENT_MAX_TOOLS,
) -> BuildToolsResult:
    """Build tools from a list of actions."""
    tools: list[Tool] = []
    collected_secrets: set[RegistrySecretType] = set()

    # Get actions from registry
    async with RegistryActionsService.with_session() as service:
        if actions:
            selected_actions = await service.get_actions(actions)
        else:
            selected_actions = await service.list_actions(include_marked=True)

        # Collect action build issues
        failed_actions: set[str] = set()
        missing_actions: set[str] = set()

        if actions:
            found_actions = {f"{ra.namespace}.{ra.name}" for ra in selected_actions}
            missing_actions = {
                action_name
                for action_name in actions
                if action_name not in found_actions
            }

        # Create tools from registry actions
        async def create_tool(ra: RegistryAction):
            action_name = f"{ra.namespace}.{ra.name}"
            logger.debug(f"Building tool for action: {action_name}")

            # Apply namespace filtering if specified
            if namespaces:
                if not any(action_name.startswith(ns) for ns in namespaces):
                    return

            # Create the tool using the extracted function
            result = await create_single_tool(service, ra, action_name, fixed_arguments)

            # Check if result is None and handle accordingly
            if result is None:
                failed_actions.add(action_name)
                return

            # Update collected secrets
            collected_secrets.update(result.collected_secrets)

            if result.tool is not None:
                tools.append(result.tool)
            else:
                failed_actions.add(result.action_name)

        # NOTE: avoid running `create_tool` concurrently with the same
        # `RegistryActionsService` instance. AsyncSession does not support
        # concurrent usage, so we iterate sequentially instead of
        # `asyncio.gather`.
        for ra in selected_actions:
            await create_tool(ra)

    # If there were failures, raise simple error
    if missing_actions or failed_actions:
        details: list[str] = []
        if missing_actions:
            missing_list = "\n".join(
                f"- {action}" for action in sorted(missing_actions)
            )
            details.append("Requested actions not found in registry:\n" + missing_list)
        if failed_actions:
            failed_list = "\n".join(f"- {action}" for action in sorted(failed_actions))
            details.append("Failed to build the following actions:\n" + failed_list)

        raise ValueError(
            "Unable to build the requested tools:\n" + "\n\n".join(details)
        )

    if max_tools > 0 and len(tools) > max_tools:
        raise ValueError(f"Cannot request more than {max_tools} tools")

    return BuildToolsResult(
        tools=tools,
        collected_secrets=collected_secrets,
    )


def _sanitize_parameter_name(name: str) -> str:
    """Sanitize parameter names that are Python reserved keywords.

    Args:
        name: The original parameter name

    Returns:
        A valid Python parameter name
    """
    if keyword.iskeyword(name):
        return f"{name}_"
    return name


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


@dataclass(frozen=True, slots=True)
class FunctionSignature:
    signature: inspect.Signature
    annotations: dict[str, Any]
    param_mapping: dict[str, str]


def _create_function_signature(
    model_cls: type, fixed_args: set[str] | None = None
) -> FunctionSignature:
    """Create function signature and annotations from a Pydantic model.

    Args:
        model_cls: The Pydantic model class
        fixed_args: Set of argument names that are fixed and should be excluded

    Returns:
        Tuple of (signature, annotations, param_mapping)
        param_mapping: dict mapping sanitized param names to original field names
    """
    sig_params = []
    annotations = {}
    param_mapping = {}  # sanitized_name -> original_field_name
    fixed_args = fixed_args or set()

    for field_name, field_info in model_cls.model_fields.items():
        # Skip fixed arguments
        if field_name in fixed_args:
            continue

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
                annotation = annotation | None
        else:
            # Required field
            default = inspect.Parameter.empty

        # Sanitize field name for Python keywords
        param_name = _sanitize_parameter_name(field_name)
        param_mapping[param_name] = field_name

        # Create parameter
        param = inspect.Parameter(
            name=param_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=annotation,
            default=default,
        )
        sig_params.append(param)
        annotations[param_name] = annotation

    # Add return annotation
    annotations["return"] = Any

    return FunctionSignature(
        signature=inspect.Signature(sig_params),
        annotations=annotations,
        param_mapping=param_mapping,
    )


def _generate_tool_function_name(namespace: str, name: str, *, sep: str = "__") -> str:
    """Generate a function name from namespace and action name.

    Args:
        namespace: The action namespace (e.g., "tools.slack")
        name: The action name (e.g., "post_message")
        sep: The separator to use between namespace and name

    Returns:
        Generated function name (e.g., "tools.slack.post_message" -> "tools__slack__post_message")
    """
    # Extract the last part of namespace after splitting by "."
    return f"{namespace}{sep}{name}".replace(".", sep)


def _generate_google_style_docstring(
    description: str | None, model_cls: type, fixed_args: set[str] | None = None
) -> str:
    """Generate a Google-style docstring from a description and Pydantic model.

    Args:
        description: The base description for the function
        model_cls: The Pydantic model class containing parameter information
        fixed_args: Set of argument names that are fixed and should be excluded

    Returns:
        A properly formatted Google-style docstring with Args section

    Raises:
        ValueError: If description is None
    """
    if description is None:
        raise ValueError("Tool description cannot be None")

    # Extract parameter descriptions from the model's JSON schema
    param_lines = []
    fixed_args = fixed_args or set()

    if hasattr(model_cls, "model_json_schema"):
        schema = model_cls.model_json_schema()
        properties = schema.get("properties", {})

        for prop_name, prop_info in properties.items():
            # Skip fixed arguments
            if prop_name in fixed_args:
                continue

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
