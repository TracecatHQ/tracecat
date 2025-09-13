"""Runbook service for freezing and replaying chats."""

import asyncio
import hashlib
import json
import textwrap
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import yaml
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tracecat_registry.integrations.pydantic_ai import build_agent, get_model

from tracecat.agent.service import AgentManagementService
from tracecat.chat.enums import ChatEntity
from tracecat.chat.models import ChatMessage, ChatResponse
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat, Runbook
from tracecat.logger import logger
from tracecat.runbook.flows import execute_runbook_for_case
from tracecat.runbook.models import (
    RunbookAlias,
    RunbookCreate,
    RunbookExecuteEntity,
    RunbookUpdate,
)
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError


class RunbookService(BaseWorkspaceService):
    """Service for managing runbooks (frozen chats)."""

    service_name = "runbook"

    def __init__(self, session: AsyncSession, role: Role):
        super().__init__(session, role)
        self.chats = ChatService(session, role)

    async def create_runbook(self, params: RunbookCreate):
        """Create a runbook."""
        if params.chat_id:
            chat = await self.chats.get_chat(params.chat_id)
            if not chat:
                raise TracecatNotFoundError(f"Chat with ID {params.chat_id} not found")
            return await self.create_runbook_from_chat(
                chat=chat, meta=params.meta, alias=params.alias
            )
        else:
            return await self.create_runbook_direct(
                title=f"New runbook - {datetime.now().isoformat()}",
                content="",
                tools=[],
                alias=params.alias,
            )

    async def create_runbook_from_chat(
        self,
        *,
        chat: Chat,
        meta: dict[str, Any] | None = None,
        alias: RunbookAlias | None = None,
    ) -> Runbook:
        """Turn a chat into a reusable runbook."""

        messages = await self.chats.get_chat_messages(chat)
        steps = self._reduce_messages_to_steps(messages)

        # Run both AI generation tasks in parallel
        results = await asyncio.gather(
            self._runbook_summary(steps, tools=chat.tools),
            self._chat_to_runbook_title(chat, meta, messages),
            return_exceptions=True,
        )

        # Process summary result
        summary: str | None
        if isinstance(results[0], Exception):
            logger.error(
                "Failed to create runbook summary",
                error=results[0],
                steps=steps,
                tools=chat.tools,
            )
            summary = None
        else:
            summary = str(results[0]) if results[0] is not None else None

        # Process title result
        title: str
        if isinstance(results[1], Exception):
            logger.warning(
                "Failed to generate title, falling back to chat title",
                error=results[1],
                chat_id=chat.id,
            )
            title = f"{chat.title}"
        else:
            title = str(results[1])

        tool_sha = self._calculate_tool_sha(chat.tools)
        token_count = self._estimate_token_count(steps)
        content = textwrap.dedent(f"""# Task
You are an expert automation agent.

You will be given a JSON <Alert>. Execute the <Steps> EXACTLY as written on that <Alert>.

Here are the <Steps> to execute:
{steps}

<StepHandling>
- user-prompt: follow the instruction
- tool-call: use the named tool; infer inputs from <Alert> and prior returns; do not reuse example values
- tool-return: note the type/shape, not literal example values
</StepHandling>

<Rules>
1. Call tools only when a <Step> says so
2. Preserve the original <Step> order
3. Never reuse example inputs/outputs; derive fresh values from <Alert>
4. No conversational chatter, rationale, or chain-of-thought; keep outputs minimal and task-focused
5. If the case is clearly unrelated to these <Steps>, stop and output INAPPLICABLE
6. Do not restate or summarize <Alert> or <Steps>
7. Keep each message under ~150 tokens; do not dump large payloads; reference them instead
</Rules>""")

        # Merge provided meta with generated meta
        runbook_meta = {
            "schema": "v1",
            "tool_sha": tool_sha,
            "token_count": token_count,
        }
        if meta:
            runbook_meta.update(meta)

        runbook = Runbook(
            title=title,
            content=content.strip(),  # Defensive
            owner_id=self.workspace_id,
            meta=runbook_meta,
            tools=chat.tools,
            summary=summary,
            alias=alias,
        )

        self.session.add(runbook)
        await self.session.commit()
        await self.session.refresh(runbook)

        logger.info(
            "Created runbook from chat",
            runbook_id=str(runbook.id),
            chat_id=chat.id,
            token_count=token_count,
            workspace_id=self.workspace_id,
        )

        return runbook

    async def _chat_to_runbook_title(
        self, chat: Chat, meta: dict[str, Any] | None, messages: list[ChatMessage]
    ) -> str:
        """Generate an ITSM-focused runbook title.

        Note: to reduce token usage, only use the first user prompt message.
        We assume the initial user prompt message and the case metadata is the most relevant.
        """
        # Extract first user message content (limited to 300 chars for efficiency)
        first_user_msg = ""
        for msg in messages[:3]:  # Check first 3 messages only
            if isinstance(msg.message, ModelRequest):
                message = msg.message  # Store in local variable for type narrowing
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and part.content:
                        first_user_msg = str(part.content)[:300]
                        break
                if first_user_msg:
                    break

        # Build ITSM-focused prompt
        case_title = meta.get("case_title", "") if meta else ""
        instructions = textwrap.dedent("""
        You are an expert ITSM runbook title specialist.
        Generate a precise 4-7 word title for this automation runbook.
        Focus on the action/resolution being automated.
        Use standard ITSM terminology (e.g., Investigate, Remediate, Configure, Deploy).
        """)

        user_prompt = f"Case: {case_title}\nUser request: {first_user_msg}\nTitle:"

        # Generate title
        svc = AgentManagementService(self.session, self.role)
        async with svc.with_model_config() as model_config:
            model = get_model(model_config.name, model_config.provider)
            agent = build_agent(
                model=model,
                instructions=instructions,
            )
            response = await agent.run(user_prompt)
            title = response.output.strip()
            # Post-process: ensure only first letter is capitalized
            if title:
                title = title.lower().capitalize()
            else:
                # Fall back to chat title
                title = f"{chat.title}"
            return title

    async def get_runbook(self, runbook_id: uuid.UUID) -> Runbook | None:
        """Get a runbook by ID."""
        stmt = select(Runbook).where(
            Runbook.id == runbook_id,
            Runbook.owner_id == self.workspace_id,
        )

        result = await self.session.exec(stmt)
        return result.first()

    async def get_prompt_by_alias(self, alias: str) -> Runbook | None:
        """Get a prompt by alias."""
        stmt = select(Runbook).where(
            Runbook.alias == alias,
            Runbook.owner_id == self.workspace_id,
        )

        result = await self.session.exec(stmt)
        return result.first()

    async def resolve_runbook_alias(self, alias: str) -> uuid.UUID | None:
        """Resolve a prompt alias to its ID."""
        stmt = select(Runbook.id).where(
            Runbook.alias == alias,
            Runbook.owner_id == self.workspace_id,
        )

        result = await self.session.exec(stmt)
        return result.one_or_none()

    async def list_runbooks(
        self,
        *,
        limit: int = 50,
        sort_by: str = "created_at",
        order: str = "desc",
    ) -> Sequence[Runbook]:
        """List runbooks for the current workspace."""
        # Determine the sort column
        sort_column = (
            col(Runbook.created_at)
            if sort_by == "created_at"
            else col(Runbook.updated_at)
        )

        # Apply sort order
        if order == "desc":
            sort_column = sort_column.desc()
        else:
            sort_column = sort_column.asc()

        stmt = (
            select(Runbook)
            .where(Runbook.owner_id == self.workspace_id)
            .order_by(sort_column)
            .limit(limit)
        )

        result = await self.session.exec(stmt)
        return result.all()

    async def update_runbook(
        self,
        runbook: Runbook,
        params: RunbookUpdate,
    ) -> Runbook:
        """Update runbook properties."""
        set_fields = params.model_dump(exclude_unset=True)
        for key, value in set_fields.items():
            setattr(runbook, key, value)

        self.session.add(runbook)
        await self.session.commit()
        await self.session.refresh(runbook)

        return runbook

    async def delete_runbook(self, runbook: Runbook) -> None:
        """Delete a runbook."""
        await self.session.delete(runbook)
        await self.session.commit()

    async def run_runbook(
        self,
        runbook: Runbook,
        entities: list[RunbookExecuteEntity],
    ) -> list[ChatResponse]:
        """Execute a runbook on multiple cases."""

        # Fire off tasks for each case
        responses = []
        for entity in entities:
            if entity.entity_type == ChatEntity.CASE:
                try:
                    response = await execute_runbook_for_case(
                        case_id=entity.entity_id,
                        runbook=runbook,
                        session=self.session,
                        role=self.role,
                    )
                except TracecatNotFoundError:
                    logger.warning(
                        "Case not found",
                        case_id=entity.entity_id,
                        runbook_id=runbook.id,
                        workspace_id=self.workspace_id,
                    )
                else:
                    responses.append(response)
        return responses

    async def create_runbook_direct(
        self,
        *,
        title: str,
        content: str,
        tools: list[str],
        summary: str | None = None,
        alias: RunbookAlias | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Runbook:
        """Create a runbook directly without a chat."""

        tool_sha = self._calculate_tool_sha(tools)
        token_count = self._estimate_token_count(content)

        runbook = Runbook(
            owner_id=self.workspace_id,
            title=title,
            content=content,
            tools=tools,
            summary=summary,
            alias=alias,
            meta={
                "created_directly": True,
                "schema": "v1",
                "tool_sha": tool_sha,
                "token_count": token_count,
                **(meta or {}),
            },
        )

        self.session.add(runbook)
        await self.session.commit()
        await self.session.refresh(runbook)

        logger.info(
            "Created direct runbook",
            runbook_id=runbook.id,
            title=title,
            tools_count=len(tools),
        )

        return runbook

    def _reduce_messages_to_steps(self, messages: list[ChatMessage]) -> str:
        """
        Reduce chat messages to a single runbook string.

        The runbook string should be an executable instruction set for an agent.

        Phase 1:
        - Just serialize as XML objects

        Phase 2:
        - Runbook optimization
        """
        # Simple concatenation approach for MVP
        runbook_parts = []

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
                    runbook_parts.append(content)
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
                    runbook_parts.append(content)
        return f"<Steps>\n{''.join(runbook_parts)}\n</Steps>"

    def _calculate_tool_sha(self, tools: list[str]) -> str:
        """Calculate SHA256 hash of tools list."""
        tools_json = json.dumps(sorted(tools), sort_keys=True)
        return hashlib.sha256(tools_json.encode()).hexdigest()

    def _estimate_token_count(self, text: str) -> int:
        """Rough estimation of token count (1 token ≈ 4 characters)."""
        return len(text) // 4

    def _clean_runbook_output(self, output: str) -> str:
        """Clean and normalize runbook output by removing code block wrappers."""
        output = output.strip()

        # Remove markdown code block wrapper if the entire content is wrapped
        if output.startswith("```") and output.endswith("```"):
            # Find the first newline after opening ```
            first_newline = output.find("\n")
            if first_newline != -1:
                # Remove opening ``` and language identifier
                output = output[first_newline + 1 :]
                # Remove closing ```
                if output.endswith("```"):
                    output = output[:-3].rstrip()
        return output

    async def _runbook_summary(self, steps: str, tools: list[str]) -> str:
        """Generate a summary for a runbook."""
        instructions = textwrap.dedent("""
        You are an expert runbook creation agent.

        You will be given a list of <Steps> by the user.
        Each <Step> in the list of <Steps> is extracted from messages between the user and an AI agent.

        <Task>
        Produce a concise, generalized runbook suitable for similar future cases.
        </Task>

        <PostProcessingRules>
        - Remove irrelevant, duplicate, or failed steps
        - Merge repeated patterns (loops over items) into one generalized step
        - Summarize each remaining step into a short, actionable instruction
        </PostProcessingRules>

        <GeneralizationRules>
        - Do NOT hardcode case-specific identifiers or values (e.g., case IDs, alert IDs, timestamps, file paths). Use placeholders (e.g., <case_id>) or JSONPath to the incoming <Alert> (e.g., $.alert.payload.host).
        - IoCs (emails, IPs, hostnames, domains, URLs, hashes, usernames) MAY appear ONLY as "example" values in the Trigger or Steps section.
        - Do not copy example tool inputs/outputs verbatim; infer parameters from intent and structure.
        - Prefer criteria/patterns/field references over literal values.
        </GeneralizationRules>

        <Requirements>
        - Output ONLY the runbook as Markdown; no prose before/after
        - Do NOT wrap the entire runbook in a single code block
        - Use Markdown: headers (#, ##), lists (-), bold (**)
        - Use fenced blocks only for actual code/commands
        - Include sections: Objective, Tools, Trigger, Steps
        - Objective must be generalized (no specific IDs/private values)
        - Tools are provided as <Tools>
        - Trigger section (Markdown):
          - Start with a single line: **Execute when**:
          - Then list 1 to 3 concise, human-readable conditions as bullets, each referencing Alert fields via JSONPath or placeholders
          - Add a **Do not execute when** subsection (optional, 0–3 bullets) listing clear exclusions
          - Keep the language human-readable; avoid raw code where possible. Use JSONPath only to point to specific fields
        </Requirements>
        """)
        svc = AgentManagementService(self.session, self.role)
        async with svc.with_model_config() as model_config:
            model = get_model(model_config.name, model_config.provider)
            agent = build_agent(
                model=model,
                instructions=instructions,
            )
            user_prompt = f"""
            {steps}

            <Tools>
            {json.dumps(tools, indent=2)}
            </Tools>
            """
            response = await agent.run(user_prompt)
            output = self._clean_runbook_output(response.output)

        return output


async def inject_runbook_content(
    *, session: AsyncSession, role: Role, runbook_id: uuid.UUID
) -> str | None:
    runbook_svc = RunbookService(session, role)
    if runbook := await runbook_svc.get_runbook(runbook_id):
        # Add indication that this is the current runbook
        # Prepare case data for YAML dump, including tags if they exist
        runbook_data = runbook.model_dump(mode="json")

        runbook_content = (
            f"This is the current runbook you are working on:\n\n"
            "<runbook_context>\n"
            f"```yaml\n"
            f"{yaml.dump(runbook_data, indent=2)}\n"
            "```\n"
            "</runbook_context>\n"
        )

        return runbook_content
    return None
