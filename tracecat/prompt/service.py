"""Prompt service for freezing and replaying chats."""

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import Sequence

import yaml
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.chat.models import ChatMessage
from tracecat.chat.service import ChatService
from tracecat.db.schemas import Chat, Prompt
from tracecat.logger import logger
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

        prompt_content = self._reduce_messages_to_prompt(messages)

        tool_sha = self._calculate_tool_sha(chat.tools)
        token_count = self._estimate_token_count(prompt_content)

        # Create prompt with default title
        prompt = Prompt(
            chat_id=chat.id,
            title=f"{chat.title} - Frozen",
            content=prompt_content,
            owner_id=self.workspace_id,
            meta={
                "schema": "v1",
                "tool_sha": tool_sha,
                "token_count": token_count,
            },
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
        case_ids: list[uuid.UUID],
    ) -> dict[str, str]:
        """Execute a prompt on multiple cases."""
        # Import here to avoid circular imports
        from tracecat_registry.integrations.agents.builder import agent

        from tracecat.secrets import secrets_manager

        # Get the chat to access tools
        stmt = select(Chat).where(Chat.id == prompt.chat_id)
        result = await self.session.exec(stmt)
        chat = result.first()
        if not chat:
            raise TracecatNotFoundError(f"Chat {prompt.chat_id} not found")

        # Prepare stream URLs
        stream_urls = {}

        # Fire off tasks for each case
        for case_id in case_ids:
            agent_args = {
                "user_prompt": prompt.content,
                "model_name": "gpt-4o",
                "model_provider": "openai",
                "actions": chat.tools,
                "workflow_run_id": case_id,  # Use case_id as workflow_run_id
            }

            # Create task with proper environment
            # NOTE: In production, this should use workspace-specific credentials
            with secrets_manager.env_sandbox(
                {"OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "")}
            ):
                _ = asyncio.create_task(agent(**agent_args))

            # Generate stream URL
            stream_urls[case_id] = f"/api/prompt/{prompt.id}/case/{case_id}/stream"

        logger.info(
            "Started prompt execution",
            prompt_id=prompt.id,
            case_count=len(case_ids),
            workspace_id=self.workspace_id,
        )

        return stream_urls

    def _reduce_messages_to_prompt(self, messages: list[ChatMessage]) -> str:
        """
        Reduce chat messages to a single prompt string.

        The prompt string should be an executable instruction set for an agent.

        Phase 1:
        - Just serialize as yaml or something

        Phase 2:
        - Prompt optimization
        """
        # Simple concatenation approach for MVP
        prompt_parts = ["You are an incident assistant helping with security cases.\n"]

        for msg in messages:
            # Extract role and content from message
            match msg.message:
                case ModelRequest(parts=parts):
                    role = (
                        "user"
                        if any(isinstance(part, UserPromptPart) for part in parts)
                        else "assistant"
                    )
                    content = yaml.dump(parts)
                    prompt_parts.append(f"{role}: {content}")
                case ModelResponse(parts=parts):
                    role = "assistant"
                    content = yaml.dump(parts)
                    prompt_parts.append(f"{role}: {content}")
        return "\n".join(prompt_parts)

    def _calculate_tool_sha(self, tools: list[str]) -> str:
        """Calculate SHA256 hash of tools list."""
        tools_json = json.dumps(sorted(tools), sort_keys=True)
        return hashlib.sha256(tools_json.encode()).hexdigest()

    def _estimate_token_count(self, text: str) -> int:
        """Rough estimation of token count (1 token ≈ 4 characters)."""
        return len(text) // 4
