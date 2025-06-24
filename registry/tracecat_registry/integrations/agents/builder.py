"""Pydantic AI agents with tool calling."""

import inspect
import textwrap
import tempfile
from pathlib import Path
import base64
from typing import Any, Union, Annotated, Self
from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from tracecat_registry.integrations.agents.parsers import try_parse_json
from pydantic_ai.agent import AgentRunResult
from typing_extensions import Doc
from timeit import timeit

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import Usage
from pydantic_ai.tools import Tool
from pydantic_core import PydanticUndefined

from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_run
from tracecat.dsl.common import create_default_execution_context
from tracecat.executor.service import (
    _run_action_direct,
    run_template_action,
    flatten_secrets,
)
from tracecat.expressions.eval import extract_templated_secrets
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.fields import ActionType, TextArea
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat_registry.integrations.pydantic_ai import (
    PYDANTIC_AI_REGISTRY_SECRETS,
    build_agent,
)

from tracecat_registry import registry, RegistrySecret
from tracecat.types.exceptions import RegistryError
from tracecat_registry.integrations.agents.exceptions import AgentRunError
from tracecat_registry.integrations.agents.tools import (
    create_secure_file_tools,
    generate_default_tools_prompt,
)


ALLOWED_TOOLS = {
    # Cases
    "core.cases.get_case",
    "core.cases.list_cases",
    "core.cases.list_comments",
    "core.cases.search_cases",
    # Read only 3rd party tools
    "tools.abuseipdb.lookup_ip_address",
    "tools.emailrep.lookup_email",
    "tools.ipinfo.lookup_ip_address",
    "tools.sublime.analyze_eml",
    "tools.sublime.analyze_url",
    "tools.sublime.binexplode",
    "tools.sublime.score_eml",
    "tools.sublime.scan_file",
    "tools.slack.list_messages",
    "tools.slack.list_replies",
    "tools.slack.lookup_user_by_email",
    "tools.tavily.web_search",
    "tools.urlhaus.list_url_threats",
    "tools.urlscan.lookup_url",
    "tools.virustotal.list_threats",
    "tools.virustotal.lookup_domain",
    "tools.virustotal.lookup_file_hash",
    "tools.virustotal.lookup_ip_address",
    "tools.virustotal.lookup_url",
    # Query engines
    "tools.splunk.search_events",
    # Write-tools with user-specified permissions
    "tools.slack.post_message",
    "tools.jira.create_issue",
    "tools.jira.add_comment",
    "core.cases.create_case",
    "core.cases.update_case",
    "core.cases.create_comment",
    "core.cases.update_comment",
}


def raise_error(error_message: str) -> None:
    """Raise an error with a custom message to be displayed to the user."""
    raise ModelRetry(error_message)


def generate_google_style_docstring(
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


async def call_tracecat_action(action_name: str, args: dict[str, Any]) -> Any:
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        action_secrets = await service.fetch_all_action_secrets(reg_action)
        bound_action = service.get_bound(reg_action, mode="execution")

    # Get runtime environment from context or use default
    runtime_env = getattr(ctx_run.get(), "environment", DEFAULT_SECRETS_ENVIRONMENT)

    # Extract secrets from args and action
    args_secrets = set(extract_templated_secrets(args))
    optional_secrets = {s.name for s in action_secrets if s.optional}
    required_secrets = {s.name for s in action_secrets if not s.optional}
    secrets_to_fetch = required_secrets | args_secrets | optional_secrets

    logger.info(
        "Handling secrets for agent tool call",
        action=action_name,
        required_secrets=required_secrets,
        optional_secrets=optional_secrets,
        args_secrets=args_secrets,
        secrets_to_fetch=secrets_to_fetch,
    )

    # Fetch all required secrets
    async with AuthSandbox(
        secrets=secrets_to_fetch,
        environment=runtime_env,
        optional_secrets=optional_secrets,
    ) as sandbox:
        secrets = sandbox.secrets.copy()

    # Call action with secrets in environment
    context = create_default_execution_context()
    context.update(SECRETS=secrets)

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
        raise ModelRetry(str(e))
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
    model_cls: type, fixed_args: set[str] | None = None
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Create function signature and annotations from a Pydantic model.

    Args:
        model_cls: The Pydantic model class
        fixed_args: Set of argument names that are fixed and should be excluded

    Returns:
        Tuple of (signature, annotations)
    """
    sig_params = []
    annotations = {}
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


async def create_tool_from_registry(
    action_name: str, fixed_args: dict[str, Any] | None = None
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
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        bound_action = service.get_bound(reg_action, mode="execution")

    fixed_args = fixed_args or {}
    fixed_arg_names = set(fixed_args.keys())

    # Create wrapper function that calls the action with fixed args merged
    async def tool_func(**kwargs) -> Any:
        # Merge fixed arguments with runtime arguments
        merged_args = {**fixed_args, **kwargs}
        return await call_tracecat_action(action_name, merged_args)

    # Set function name
    tool_func.__name__ = _generate_tool_function_name(
        bound_action.namespace, bound_action.name
    )

    # Extract metadata from the bound action
    description, model_cls = _extract_action_metadata(bound_action)

    # Validate description
    if not description:
        raise ValueError(f"Action '{action_name}' has no description")

    # Create function signature and annotations, excluding fixed args
    signature, annotations = _create_function_signature(model_cls, fixed_arg_names)
    tool_func.__signature__ = signature
    tool_func.__annotations__ = annotations

    # Generate Google-style docstring, excluding fixed args
    tool_func.__doc__ = generate_google_style_docstring(
        description, model_cls, fixed_arg_names
    )

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
        fixed_arguments: dict[str, dict[str, Any]] | None = None,
    ):
        self.model_name = model_name
        self.model_provider = model_provider
        self.base_url = base_url
        self.instructions = instructions
        self.output_type = output_type
        self.model_settings = model_settings
        self.retries = retries
        self.deps_type = deps_type
        self.fixed_arguments = fixed_arguments or {}
        self.tools: list[Tool] = []
        self.namespace_filters: list[str] = []
        self.action_filters: list[str] = []
        self.collected_secrets: set[RegistrySecret] = set()

    def with_namespace_filters(self, *namespaces: str) -> Self:
        """Add namespace filters for tools (e.g., 'tools.slack', 'tools.email')."""
        self.namespace_filters.extend(namespaces)
        return self

    def with_action_filters(self, *action_names: str) -> Self:
        """Add action name filters (e.g., 'tools.slack.send_message', 'tools.email.send_email')."""
        self.action_filters.extend(action_names)
        return self

    def with_custom_tool(self, tool: Tool) -> Self:
        """Add a custom Pydantic AI tool."""
        self.tools.append(tool)
        return self

    def with_default_tools(self, temp_dir: str | None = None) -> Self:
        """Add default file manipulation tools, optionally restricted to temp_dir."""
        if temp_dir:
            # Use secure tools restricted to temp_dir
            secure_tools = create_secure_file_tools(temp_dir)
            self.tools.extend(secure_tools)
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
                # Fetch all secrets for this action
                async with RegistryActionsService.with_session() as service:
                    action_secrets = await service.fetch_all_action_secrets(reg_action)
                    self.collected_secrets.update(action_secrets)

                # Determine if we should pass fixed arguments to the helper
                has_any_fixed_args = bool(self.fixed_arguments)
                action_fixed_args = self.fixed_arguments.get(action_name)

                if has_any_fixed_args:
                    # If some fixed arguments were supplied when constructing the builder,
                    # we always include the second parameter – pass an empty dict when the
                    # current action does not have any overrides so that callers may rely on
                    # the two-argument form when the feature is in use.
                    action_fixed_args = action_fixed_args or {}
                    tool = await create_tool_from_registry(
                        action_name, action_fixed_args
                    )
                else:
                    # No fixed arguments functionality requested – keep the original
                    # single-parameter call signature expected by existing tests.
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

        # Add raise_error tool
        self.tools.append(
            Tool(
                name="raise_error",
                description="Raise an error with a custom message to be displayed to the user.",
                function=raise_error,
            )
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
            secret_count=len(self.collected_secrets),
        )
        return agent


async def collect_secrets_for_filters(
    namespace_filters: list[str] | None = None,
    action_filters: list[str] | None = None,
) -> set[RegistrySecret]:
    """Collect all secrets required by actions matching the given filters."""
    collected_secrets: set[RegistrySecret] = set()

    async with RegistryActionsService.with_session() as service:
        if action_filters:
            actions = await service.get_actions(action_filters)
        else:
            actions = await service.list_actions(include_marked=True)

    for reg_action in actions:
        action_name = f"{reg_action.namespace}.{reg_action.name}"

        # Apply namespace filtering if specified
        if namespace_filters:
            if not any(action_name.startswith(ns) for ns in namespace_filters):
                continue

        # Fetch all secrets for this action
        async with RegistryActionsService.with_session() as service:
            action_secrets = await service.fetch_all_action_secrets(reg_action)
            collected_secrets.update(action_secrets)

    return collected_secrets


class AgentOutput(BaseModel):
    output: Any
    files: dict[str, str] | None = None
    message_history: list[ModelMessage]
    duration: float
    usage: Usage | None = None


@registry.register(
    default_title="AI agent",
    description="AI agent with tool calling capabilities. Returns the output and full message history.",
    display_group="AI",
    doc_url="https://ai.pydantic.dev/agents/",
    secrets=[*PYDANTIC_AI_REGISTRY_SECRETS],
    namespace="ai",
)
async def agent(
    user_prompt: Annotated[
        str,
        Doc("User prompt to the agent."),
        TextArea(),
    ],
    model_name: Annotated[str, Doc("Name of the model to use.")],
    model_provider: Annotated[str, Doc("Provider of the model to use.")],
    actions: Annotated[
        list[str] | str,
        Doc("Actions (e.g. 'tools.slack.post_message') to include in the agent."),
        ActionType(multiple=True),
    ],
    files: Annotated[
        dict[str, str] | None,
        Doc(
            "Files to include in the agent's temporary directory environment. Keys are file paths and values are base64-encoded file contents."
        ),
    ] = None,
    fixed_arguments: Annotated[
        dict[str, dict[str, Any]] | None,
        Doc(
            "Fixed action arguments: keys are action names, values are keyword arguments. "
            "E.g. {'tools.slack.post_message': {'channel_id': 'C123456789', 'text': 'Hello, world!'}}"
        ),
    ] = None,
    instructions: Annotated[
        str | None, Doc("Instructions for the agent."), TextArea()
    ] = None,
    output_type: Annotated[
        str | dict[str, Any] | None, Doc("Output type for the agent.")
    ] = None,
    model_settings: Annotated[
        dict[str, Any] | None, Doc("Model settings for the agent.")
    ] = None,
    retries: Annotated[int, Doc("Number of retries for the agent.")] = 6,
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
        output_type=output_type,
        model_settings=model_settings,
        retries=retries,
        fixed_arguments=fixed_arguments,
    )

    if not actions:
        raise ValueError("No actions provided. Please provide at least one action.")

    if isinstance(actions, str):
        actions = [actions]

    blocked_actions = set(actions) - ALLOWED_TOOLS
    if len(blocked_actions) > 0:
        raise ValueError(f"Forbidden actions: {blocked_actions}")

    # Generate the enhanced user prompt with tool guidance
    tools_prompt = generate_default_tools_prompt(files) if files else ""
    # Provide current date context using Tracecat expression
    current_date_prompt = "<current_date>${{ FN.utcnow() }}</current_date>"
    error_handling_prompt = """
    <error_handling>
    - Use `raise_error` when a <task> or any task-like instruction requires clarification or missing information
    - Be specific about what's needed: "Missing API key" not "Cannot proceed"
    - Stop execution immediately - don't attempt workarounds or assumptions
    </error_handling>
    """

    # Build the final enhanced user prompt including date context
    if tools_prompt:
        enhanced_user_prompt = f"{user_prompt}\n{current_date_prompt}\n{tools_prompt}\n{error_handling_prompt}"
    else:
        enhanced_user_prompt = (
            f"{user_prompt}\n{current_date_prompt}\n{error_handling_prompt}"
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        if files:
            for path, content in files.items():
                file_path = Path(temp_dir) / path
                file_path.write_bytes(base64.b64decode(content))

            # Add secure default tools with temp_dir restriction
            builder = builder.with_default_tools(temp_dir)

        agent = await builder.with_action_filters(*actions).build()

        start_time = timeit()
        # Use async version since this function is already async
        try:
            message_nodes = []
            async with agent.iter(user_prompt=enhanced_user_prompt) as run:
                async for node in run:
                    message_nodes.append(to_jsonable_python(node))
                result = run.result
                if not isinstance(result, AgentRunResult):
                    raise ValueError("No output returned from agent run.")
        except Exception as e:
            raise AgentRunError(
                exc_cls=type(e),
                exc_msg=str(e),
                message_history=message_nodes,
            )
        end_time = timeit()

        # Read potentially modified files from temp directory
        files = {}
        for file_path in Path(temp_dir).glob("*"):
            with open(file_path, "r") as f:
                base64_content = base64.b64encode(f.read().encode()).decode()
                files[file_path.name] = base64_content

        output = AgentOutput(
            output=try_parse_json(result.output),
            files=files,
            message_history=result.all_messages(),
            duration=end_time - start_time,
        )
        if include_usage:
            output.usage = result.usage()

    return output.model_dump()
