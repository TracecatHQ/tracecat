"""Pydantic AI agents with tool calling."""

import inspect
import textwrap
import tempfile
import uuid
from pathlib import Path
import base64
from typing import Any, Union, Annotated, Self
from pydantic import BaseModel, TypeAdapter
from pydantic_core import to_json, to_jsonable_python
from tracecat_registry import RegistrySecretType
from tracecat_registry.integrations.agents.parsers import try_parse_json
from pydantic_ai.agent import AgentRunResult
from typing_extensions import Doc
from timeit import timeit
import orjson

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import Usage
from pydantic_ai.tools import Tool
from pydantic_core import PydanticUndefined

from tracecat.dsl.common import create_default_execution_context
from tracecat.executor.service import (
    _run_action_direct,
    get_action_secrets,
    run_template_action,
    flatten_secrets,
)
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.fields import ActionType, TextArea
from tracecat.secrets.secrets_manager import env_sandbox
from tracecat_registry.integrations.pydantic_ai import (
    PYDANTIC_AI_REGISTRY_SECRETS,
    build_agent,
)

from tracecat_registry import registry
from tracecat.types.exceptions import RegistryError
from tracecat_registry.integrations.agents.exceptions import AgentRunError
from tracecat_registry.integrations.agents.tools import (
    create_secure_file_tools,
    create_tool_call,
    create_tool_return,
    generate_default_tools_prompt,
)


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


async def load_conversation_history(
    redis_client, conversation_id: str
) -> list[ModelMessage]:
    """Load conversation history from Redis stream.

    Args:
        redis_client: Redis client instance
        conversation_id: The conversation ID

    Returns:
        List of reconstructed ModelMessage objects
    """
    stream_key = f"agent-stream:{conversation_id}"

    try:
        # Read all messages from the stream
        messages = await redis_client.xrange(stream_key, min_id="-", max_id="+")

        history: list[ModelMessage] = []

        for message_id, fields in messages:
            try:
                data = orjson.loads(fields["d"])

                # Skip non-message entries
                if data.get("__end__") == 1:
                    continue

                # Reconstruct message based on type
                msg_type = data.get("type")

                if msg_type == "human":
                    # Create user message
                    content = data.get("content", "")
                    history.append(ModelRequest(parts=[UserPromptPart(content)]))
                elif msg_type == "ai":
                    # Create AI response
                    content = data.get("content", "")
                    if content:  # Only add if there's content
                        history.append(ModelResponse(parts=[TextPart(content)]))
                elif msg_type == "tool-call":
                    # Create tool call message
                    tool_name = data.get("tool_name", "")
                    tool_args = data.get("args_json", {})
                    if isinstance(tool_args, str):
                        tool_args = orjson.loads(tool_args)

                    history.append(
                        ModelResponse(
                            parts=[
                                ToolCallPart(
                                    tool_name=tool_name,
                                    args=tool_args,
                                    tool_call_id=data.get(
                                        "tool_call_id", str(uuid.uuid4())
                                    ),
                                )
                            ]
                        )
                    )
                elif msg_type == "tool-return":
                    # Create tool return message
                    tool_name = data.get("tool_name", "")
                    tool_call_id = data.get("tool_call_id", "")
                    content = data.get("content", "")

                    history.append(
                        ModelRequest(
                            parts=[
                                ToolReturnPart(
                                    tool_name=tool_name,
                                    tool_call_id=tool_call_id,
                                    content=content,
                                )
                            ]
                        )
                    )

            except Exception as e:
                logger.warning(
                    "Failed to reconstruct message from history",
                    message_id=message_id,
                    error=str(e),
                )
                continue

        logger.info(
            "Loaded conversation history",
            conversation_id=conversation_id,
            message_count=len(history),
        )

        return history

    except Exception as e:
        logger.error(
            "Failed to load conversation history",
            conversation_id=conversation_id,
            error=str(e),
        )
        return []


def serialize_message_for_storage(message: ModelMessage) -> dict[str, Any]:
    """Serialize a ModelMessage for storage in Redis.

    Args:
        message: The ModelMessage to serialize

    Returns:
        Dictionary with message data for storage
    """
    if isinstance(message, ModelRequest):
        # Handle user messages
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                return {
                    "type": "human",
                    "content": part.content,
                }
            elif isinstance(part, ToolReturnPart):
                return {
                    "type": "tool-return",
                    "tool_name": part.tool_name,
                    "tool_call_id": part.tool_call_id,
                    "content": part.content,
                }
    elif isinstance(message, ModelResponse):
        # Handle AI responses
        for part in message.parts:
            if isinstance(part, TextPart):
                return {
                    "type": "ai",
                    "content": part.content,
                }
            elif isinstance(part, ToolCallPart):
                return {
                    "type": "tool-call",
                    "tool_name": part.tool_name,
                    "args_json": orjson.dumps(part.args).decode(),
                    "tool_call_id": part.tool_call_id,
                }

    # Fallback for unknown message types
    return {
        "type": "unknown",
        "data": str(message),
    }


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
        self.collected_secrets: set[RegistrySecretType] = set()

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
        # self.tools.append(
        #     Tool(
        #         name="raise_error",
        #         description="Raise an error with a custom message to be displayed to the user.",
        #         function=raise_error,
        #     )
        # )

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


# TODO: unused
async def collect_secrets_for_filters(
    namespace_filters: list[str] | None = None,
    action_filters: list[str] | None = None,
) -> set[RegistrySecretType]:
    """Collect all secrets required by actions matching the given filters."""
    collected_secrets: set[RegistrySecretType] = set()

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


ModelMessageTA: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


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
    workflow_run_id: Annotated[
        str | None,
        Doc(
            "Workflow run ID for Redis streaming. If provided with action_ref, enables real-time streaming."
        ),
    ] = None,
) -> dict[str, str | dict[str, Any] | list[dict[str, Any]]]:
    # Only enhance instructions when provided (not None)
    enhanced_instrs: str | None = None
    if instructions is not None:
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
            enhanced_instrs = f"{instructions}\n{current_date_prompt}\n{tools_prompt}\n{error_handling_prompt}"
        else:
            enhanced_instrs = (
                f"{instructions}\n{current_date_prompt}\n{error_handling_prompt}"
            )
    builder = TracecatAgentBuilder(
        model_name=model_name,
        model_provider=model_provider,
        base_url=base_url,
        instructions=enhanced_instrs,
        output_type=output_type,
        model_settings=model_settings,
        retries=retries,
        fixed_arguments=fixed_arguments,
    )

    if not actions:
        raise ValueError("No actions provided. Please provide at least one action.")

    if isinstance(actions, str):
        actions = [actions]

    with tempfile.TemporaryDirectory() as temp_dir:
        if files:
            for path, content in files.items():
                file_path = Path(temp_dir) / path
                file_path.write_bytes(base64.b64decode(content))

            # Add secure default tools with temp_dir restriction
            builder = builder.with_default_tools(temp_dir)

        agent = await builder.with_action_filters(*actions).build()

        start_time = timeit()
        # Set up Redis streaming if both parameters are provided
        redis_client = None
        stream_key = None
        conversation_history: list[ModelMessage] = []

        if workflow_run_id:
            stream_key = f"agent-stream:{workflow_run_id}"
            try:
                redis_client = await get_redis_client()
                logger.info("Redis streaming enabled", stream_key=stream_key)

                messages = await redis_client.xrange(stream_key, min_id="-", max_id="+")

                for message_id, fields in messages:
                    try:
                        data = orjson.loads(fields["d"])
                        if data.get("__end__") == 1:
                            # This is an end-of-stream marker, skip
                            continue

                        validated_msg = ModelMessageTA.validate_python(data)
                        conversation_history.append(validated_msg)
                    except Exception as e:
                        logger.warning("Failed to load message", error=str(e))

            except Exception as e:
                logger.warning(
                    "Failed to initialize Redis client, continuing without streaming",
                    error=str(e),
                )

        # Use async version since this function is already async
        async def write_to_redis(msg: ModelMessage):
            # Stream to Redis if enabled
            if redis_client and stream_key:
                logger.debug("Streaming message to Redis", stream_key=stream_key)
                try:
                    await redis_client.xadd(
                        stream_key,
                        {"d": orjson.dumps(msg, default=to_jsonable_python).decode()},
                        maxlen=10000,
                        approximate=True,
                    )
                except Exception as e:
                    logger.warning("Failed to stream message to Redis", error=str(e))

        try:
            message_nodes: list[ModelMessage] = []
            # Pass conversation history to the agent
            async with agent.iter(
                user_prompt=user_prompt,
                message_history=[
                    ModelResponse(
                        parts=[
                            TextPart(
                                content=f"Chat history thus far: <chat_history>{to_json(conversation_history, indent=2).decode()}</chat_history>"
                            )
                        ]
                    ),
                ],
            ) as run:
                async for node in run:
                    curr: ModelMessage
                    if Agent.is_user_prompt_node(node):
                        continue

                    # 1️⃣  Model request (may be a normal user/tool-return message)
                    elif Agent.is_model_request_node(node):
                        curr = node.request

                        # If this request is ONLY a tool-return we have
                        # already streamed it via FunctionToolResultEvent.
                        if any(isinstance(p, ToolReturnPart) for p in curr.parts):
                            message_nodes.append(curr)  # keep history
                            continue  # ← skip duplicate stream
                    # assistant tool-call + tool-return events
                    elif Agent.is_call_tools_node(node):
                        curr = node.model_response
                        async with node.stream(run.ctx) as stream:
                            async for event in stream:
                                if isinstance(event, FunctionToolCallEvent):
                                    message = create_tool_call(
                                        tool_name=event.part.tool_name,
                                        tool_args=event.part.args,
                                        tool_call_id=event.part.tool_call_id,
                                    )
                                elif isinstance(
                                    event, FunctionToolResultEvent
                                ) and isinstance(event.result, ToolReturnPart):
                                    message = create_tool_return(
                                        tool_name=event.result.tool_name,
                                        content=event.result.content,
                                        tool_call_id=event.tool_call_id,
                                    )
                                else:
                                    continue

                                message_nodes.append(message)
                                await write_to_redis(message)
                        continue
                    elif Agent.is_end_node(node):
                        final = node.data
                        if final.tool_name:
                            curr = create_tool_return(
                                tool_name=final.tool_name,
                                content=final.output,
                                tool_call_id=final.tool_call_id or "",
                            )
                        else:
                            # Plain text output
                            curr = ModelResponse(
                                parts=[
                                    TextPart(content=final.output),
                                ]
                            )
                    else:
                        raise ValueError(f"Unknown node type: {node}")

                    message_nodes.append(curr)
                    await write_to_redis(curr)

                result = run.result
                if not isinstance(result, AgentRunResult):
                    raise ValueError("No output returned from agent run.")

            # Add end-of-stream marker if streaming is enabled
            if redis_client and stream_key:
                try:
                    await redis_client.xadd(
                        stream_key,
                        {"d": orjson.dumps({"__end__": 1}).decode()},
                        maxlen=10000,
                        approximate=True,
                    )
                    logger.info("Added end-of-stream marker", stream_key=stream_key)
                except Exception as e:
                    logger.warning("Failed to add end-of-stream marker", error=str(e))

        except Exception as e:
            raise AgentRunError(
                exc_cls=type(e),
                exc_msg=str(e),
                message_history=[to_jsonable_python(m) for m in message_nodes],
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
