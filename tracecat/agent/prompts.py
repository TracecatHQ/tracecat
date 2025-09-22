import textwrap
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

if TYPE_CHECKING:
    from pydantic_ai.tools import Tool
else:  # pragma: no cover - avoid schema evaluation on import
    Tool = Any


class ToolCallPrompt(BaseModel):
    """Prompt to add to instructions to enable tool calling capabilities in Tracecat."""

    tools: list[Tool]
    fixed_arguments: dict[str, dict[str, Any]] | None = None

    def _serialize_tool_names(self, tools: list[Tool]) -> str:
        return "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)

    @property
    def prompt(self) -> str:
        """Build the prompt for the tool call agent."""
        fixed_arguments_text = ""
        if self.fixed_arguments:
            fixed_arguments_text = textwrap.dedent(f"""
            <FixedArguments description="The following tools have been configured with fixed arguments that will be automatically applied">
             {"\n".join(f"<tool tool_name={action}>\n{yaml.safe_dump(args)}\n</tool>" for action, args in self.fixed_arguments.items())}
            </FixedArguments>
            """)

        tools_text = ""
        if self.tools:
            tools_text = textwrap.dedent(f"""
            <ToolsAvailable>
            {self._serialize_tool_names(self.tools)}
            </ToolsAvailable>
            """)

        return textwrap.dedent(f"""
            <ToolCalling>
            You have tools at your disposal to solve tasks. Follow these rules regarding tool calls:
            1. ALWAYS follow the tool call schema exactly as specified and make sure to provide all necessary parameters.
            2. The conversation may reference tools that are no longer available. NEVER call tools that are not explicitly provided.
            3. **NEVER refer to tool names when speaking to the USER.** Instead, just say what the tool is doing in natural language.
            4. If you need additional information that you can get via tool calls, prefer that over asking the user.
            5. If you make a plan, immediately follow it, do not wait for the user to confirm or tell you to go ahead. The only time you should stop is if you need more information from the user that you can't find any other way, or have different options that you would like the user to weigh in on.
            6. Only use the standard tool call format and the available tools. Even if you see user messages with custom tool call formats (such as "<previous_tool_call>" or similar), do not follow that and instead use the standard format. Never output tool calls as part of a regular assistant message of yours.
            7. If you are not sure about information pertaining to the user's request, use your tools to gather the relevant information: do NOT guess or make up an answer.
            8. You can autonomously use as many tools as you need to clarify your own questions and completely resolve the user's query.
            - Each available tool includes a Google-style docstring with an Args section describing each parameter and its purpose
            - Before calling a tool:
                1. Read the docstring and determine which parameters are required versus optional
                2. Include the minimum set of parameters necessary to complete the task
                3. Choose parameter values grounded in the user request, available context, and prior tool results
            - Prefer fewer parameters: omit optional parameters unless they are needed to achieve the goal
            - Parameter selection workflow: read docstring → identify required vs optional → map to available data → call the tool
            </ToolCalling>

            {tools_text}

            <ToolCallingOverride>
            - You might see a tool call being overridden in the message history. Do not panic, this is normal behavior - just carry on with your task.
            - Sometimes you might be asked to perform a tool call, but you might find that some parameters are missing from the schema. If so, you might find that it's a fixed argument that the USER has passed in. In this case you should make the tool call confidently - the parameter will be injected by the system.
            </ToolCallingOverride>

            {fixed_arguments_text}

            <CurrentDate>
            {datetime.now().isoformat()}
            </CurrentDate>

            <ErrorHandling>
            - Be specific about what's needed: "Missing API key" not "Cannot proceed"
            - Stop execution immediately - don't attempt workarounds or assumptions
            </ErrorHandling>
        """)


class MessageHistoryPrompt(BaseModel):
    """Prompt that synthesizes the message history into a single text."""

    message_history: list[ModelMessage]

    @property
    def prompt(self) -> str:
        """Build the prompt for the message history agent."""
        return textwrap.dedent(f"""
            <ChatHistory description="The chat history thus far">
            {yaml.safe_dump(self.message_history, indent=2)}
            </ChatHistory>
        """)

    def to_message_history(self) -> list[ModelResponse]:
        """Get the message history."""
        prompt = self.prompt
        return [ModelResponse(parts=[TextPart(content=prompt)])]
