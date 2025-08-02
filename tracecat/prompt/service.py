"""Prompt service for freezing and replaying chats."""

import hashlib
import json
import textwrap
import uuid
from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry.integrations.pydantic_ai import build_agent

from tracecat.agent.service import AgentManagementService
from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatMessage, ChatResponse
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat, Prompt
from tracecat.logger import logger
from tracecat.prompt.flows import execute_runbook_for_case
from tracecat.prompt.models import PromptRunEntity
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


class PromptService(BaseWorkspaceService):
    """Service for managing prompts (frozen chats)."""

    service_name = "prompt"

    def __init__(self, session: AsyncSession, role: Role):
        super().__init__(session, role)
        self.chats = ChatService(session, role)

    async def create_prompt(
        self,
        *,
        chat: Chat,
    ) -> Prompt:
        """Turn a chat into a reusable prompt."""

        messages = await self.chats.get_chat_messages(chat)

        steps = self._reduce_messages_to_steps(messages)

        try:
            summary = await self._prompt_summary(steps, tools=chat.tools)
        except Exception as e:
            logger.error(
                "Failed to create prompt summary",
                error=e,
                steps=steps,
                tools=chat.tools,
            )
            summary = None

        tool_sha = self._calculate_tool_sha(chat.tools)
        token_count = self._estimate_token_count(steps)

        # Create prompt with default title
        content = textwrap.dedent(f"""
# Task
You are an expert automation agent.

You will be given a JSON <Alert> by the user.
Execute each <Step> in the list of <Steps> EXACTLY as they appear, on the user-provided <Alert>.

Here are the steps that you need to execute:
{steps}

It is important you note that these steps contain example tool call inputs and outputs that you should not reuse on the user-provided <Alert>.
You must determine yourself the inputs to each tool call, and observe the responses.

## Handling steps
If a <Step> is ...

... a user-prompt: follow the user's instructions

... a tool-call: pay attention to the tool name, understand where the arguments came from

... a tool-return: pay attention to the tool name and what *kind* of data is returned, but not the actual _value_ of the data.

Sticking to the above will help you successfully run the <Steps> over the new user-provided <Alert>

<Rules>
1. Do NOT call tools outside of <Steps>
2. Preserve the order of <Steps>
3. Do NOT use any data from the example <Steps> in the task, as this is only an example of a successful run. Instead you must use data only from the user-provided <Alert>
4. Do NOT add conversational chatter
5. When using Splunk tools, use `verify_ssl=false`
6. Do your best to interpret the user's instructions, but if you are unsure, ask the user for clarification. For example, you shouldn't write all your thoughts to the case comments if not asked to do so.
</Rules>

        """)
        prompt = Prompt(
            chat_id=chat.id,
            title=f"{chat.title} - Runbook",
            content=content,
            owner_id=self.workspace_id,
            meta={
                "schema": "v1",
                "tool_sha": tool_sha,
                "token_count": token_count,
            },
            tools=chat.tools,
            summary=summary,
        )

        self.session.add(prompt)
        await self.session.commit()
        await self.session.refresh(prompt)

        logger.info(
            "Created prompt from chat",
            prompt_id=str(prompt.id),
            chat_id=chat.id,
            token_count=token_count,
            workspace_id=self.workspace_id,
        )

        return prompt

    async def get_prompt(self, prompt_id: uuid.UUID) -> Prompt | None:
        """Get a prompt by ID."""
        stmt = select(Prompt).where(
            Prompt.id == prompt_id,
            Prompt.owner_id == self.workspace_id,
        )

        result = await self.session.exec(stmt)
        return result.first()

    async def list_prompts(
        self,
        *,
        limit: int = 50,
    ) -> Sequence[Prompt]:
        """List prompts for the current workspace."""
        stmt = (
            select(Prompt)
            .where(Prompt.owner_id == self.workspace_id)
            .order_by(col(Prompt.created_at).desc())
            .limit(limit)
        )

        result = await self.session.exec(stmt)
        return result.all()

    async def update_prompt(
        self,
        prompt: Prompt,
        *,
        title: str | None = None,
        content: str | None = None,
        tools: list[str] | None = None,
    ) -> Prompt:
        """Update prompt properties."""
        if title is not None:
            prompt.title = title

        if content is not None:
            prompt.content = content

        if tools is not None:
            prompt.tools = tools

        self.session.add(prompt)
        await self.session.commit()
        await self.session.refresh(prompt)

        return prompt

    async def delete_prompt(self, prompt: Prompt) -> None:
        """Delete a prompt."""
        await self.session.delete(prompt)
        await self.session.commit()

    async def run_prompt(
        self,
        prompt: Prompt,
        entities: list[PromptRunEntity],
    ) -> list[ChatResponse]:
        """Execute a prompt on multiple cases."""

        # Fire off tasks for each case
        responses = []
        for entity in entities:
            if entity.entity_type == ChatEntity.CASE:
                try:
                    response = await execute_runbook_for_case(
                        case_id=entity.entity_id,
                        prompt=prompt,
                        session=self.session,
                        role=self.role,
                    )
                except TracecatNotFoundError:
                    logger.warning(
                        "Case not found",
                        case_id=entity.entity_id,
                        prompt_id=prompt.id,
                        workspace_id=self.workspace_id,
                    )
                else:
                    responses.append(response)
        return responses

    def _reduce_messages_to_steps(self, messages: list[ChatMessage]) -> str:
        """
        Reduce chat messages to a single prompt string.

        The prompt string should be an executable instruction set for an agent.

        Phase 1:
        - Just serialize as XML objects

        Phase 2:
        - Prompt optimization
        """
        # Simple concatenation approach for MVP
        prompt_parts = []

        # Turn these into steps
        for msg in messages:
            # Extract role and content from message
            match msg.message:
                case ModelRequest(parts=parts):
                    xml_parts = []
                    for part in parts:
                        match part:
                            case UserPromptPart(content=content):
                                xml_parts.append(
                                    f'<Step type="user-prompt">\n'
                                    f'\t<Content type="json">{json.dumps(content, indent=2)}</Content>\n'
                                    "</Step>\n"
                                )
                            case ToolReturnPart(tool_name=tool_name, content=content):
                                xml_parts.append(
                                    f'<Step type="tool-return" tool_name="{tool_name}">\n'
                                    f'\t<Content type="json">{json.dumps(content, indent=2)}</Content>\n'
                                    "</Step>\n"
                                )
                    content = "".join(xml_parts)
                    prompt_parts.append(content)
                case ModelResponse(parts=parts):
                    # Convert each part to XML
                    xml_parts = []
                    for part in parts:
                        match part:
                            case ToolCallPart(tool_name=tool_name, args=args):
                                xml_parts.append(
                                    f'<Step type="tool-call" tool_name="{tool_name}">\n'
                                    f'\t<Args type="json">{json.dumps(args, indent=2)}</Args>\n'
                                    "</Step>\n"
                                )
                    content = "".join(xml_parts)
                    prompt_parts.append(content)
        return f"<Steps>\n{''.join(prompt_parts)}\n</Steps>"

    def _calculate_tool_sha(self, tools: list[str]) -> str:
        """Calculate SHA256 hash of tools list."""
        tools_json = json.dumps(sorted(tools), sort_keys=True)
        return hashlib.sha256(tools_json.encode()).hexdigest()

    def _estimate_token_count(self, text: str) -> int:
        """Rough estimation of token count (1 token ≈ 4 characters)."""
        return len(text) // 4

    async def _prompt_summary(self, steps: str, tools: list[str]) -> str:
        """Convert a prompt to a runbook."""
        instructions = textwrap.dedent("""
        You are an expert runbook creation agent.

        You will be given a list of <Steps> by the user.
        Each <Step> in the list of <Steps> is extracted from messages between the user and an AI agent.

        <Task>
        Your task is to post-process the <Steps> into a generalized runbook.
        </Task>

        <PostProcessingRules>
        - Remove steps that are not relevant to achieving the user's objective
        - Remove duplicate steps
        - Remove failed steps (e.g. malformed tool calls)
        - Combine steps that are related or part of a "loop" (e.g. multiple tool calls for a list of items)
        - Generalize and summarize each <Step> into a concise task description that a human or agent can follow
        </PostProcessingRules>

        <Requirements>
        - Return ONLY the runbook as a formatted markdown string, nothing else
        - Include a section `Objective` on the user's objective. You must infer the objective from the <Steps>.
        - Include a section `Tools` on the tools that are required for the runbook. This will be provided by the user as <Tools>.
        </Requirements>
        """)
        svc = AgentManagementService(self.session, self.role)
        async with svc.with_model_config() as model_config:
            agent = build_agent(
                model_name=model_config.name,
                model_provider=model_config.provider,
                instructions=instructions,
            )
            user_prompt = f"""
            {steps}

            <Tools>
            {json.dumps(tools, indent=2)}
            </Tools>
            """
            response = await agent.run(user_prompt)
        return response.output
