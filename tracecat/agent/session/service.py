"""Service for managing agent sessions."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import orjson
from pydantic_ai.messages import (
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai.tools import ToolApproved, ToolDenied
from sqlalchemy import delete, select

import tracecat.agent.adapter.vercel
from tracecat import config
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.preset.prompts import AgentPresetBuilderPrompt
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session.schemas import (
    AgentSessionCreate,
    AgentSessionRead,
    AgentSessionUpdate,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig, ClaudeSDKMessageTA
from tracecat.audit.logger import audit_log
from tracecat.cases.prompts import CaseCopilotPrompts
from tracecat.cases.service import CasesService
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import (
    ApprovalRead,
    BasicChatRequest,
    ChatMessage,
    ChatReadMinimal,
    ChatRequest,
    ChatResponse,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.chat.service import ChatService
from tracecat.chat.tools import get_default_tools
from tracecat.db.models import AgentSession, AgentSessionHistory, Approval, Chat
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import UserID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.workspaces.prompts import WorkspaceCopilotPrompts

if TYPE_CHECKING:
    from tracecat.agent.executor.activity import ToolExecutionResult


@dataclass
class SessionHistoryData:
    """Data structure for session history loaded from DB."""

    sdk_session_id: str
    sdk_session_data: str
    is_fork: bool = False  # If True, SDK should use fork_session=True


class AgentSessionService(BaseWorkspaceService):
    """Service for managing agent sessions and history."""

    service_name = "agent-session"

    async def create_session(
        self,
        args: AgentSessionCreate,
    ) -> AgentSession:
        """Create a new agent session.

        Args:
            args: Session creation parameters.

        Returns:
            The created AgentSession model.
        """
        # Apply default tools based on entity type if tools not provided
        tools = args.tools
        if not tools and args.entity_type:
            tools = get_default_tools(args.entity_type.value)

        agent_session = AgentSession(
            workspace_id=self.workspace_id,
            # Metadata
            title=args.title,
            created_by=self.role.user_id,
            entity_type=args.entity_type.value,
            entity_id=args.entity_id,
            tools=tools,
            agent_preset_id=args.agent_preset_id,
            # Harness
            harness_type=args.harness_type,
        )
        # Use provided ID if given, otherwise DB default generates one
        if args.id:
            agent_session.id = args.id
        self.session.add(agent_session)
        await self.session.commit()
        await self.session.refresh(agent_session)
        return agent_session

    async def get_session(
        self,
        session_id: uuid.UUID,
    ) -> AgentSession | None:
        """Get an agent session by ID.

        Only returns actual AgentSession records. Use get_legacy_chat()
        for legacy Chat records.

        Args:
            session_id: The session UUID.

        Returns:
            AgentSession model if found, None otherwise.
        """
        stmt = select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_legacy_chat(
        self,
        session_id: uuid.UUID,
    ) -> Chat | None:
        """Get a legacy Chat by ID.

        Args:
            session_id: The chat UUID.

        Returns:
            Chat model if found, None otherwise.
        """
        stmt = select(Chat).where(
            Chat.id == session_id,
            Chat.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_legacy_session(self, session_id: uuid.UUID) -> bool:
        """Check if a session ID refers to a legacy Chat record.

        Args:
            session_id: The session/chat UUID.

        Returns:
            True if this is a legacy Chat, False otherwise.
        """
        chat = await self.get_legacy_chat(session_id)
        return chat is not None

    async def get_or_create_session(
        self,
        args: AgentSessionCreate,
    ) -> tuple[AgentSession, bool]:
        """Get an existing session or create a new one.

        Looks up by session ID to find existing sessions.

        Args:
            args: Session creation parameters.

        Returns:
            Tuple of (AgentSession, created) where created is True if new.
        """
        if args.id is not None:
            existing = await self.get_session(args.id)
            if existing:
                return existing, False
        new_session = await self.create_session(args)
        return new_session, True

    async def list_sessions(
        self,
        *,
        created_by: UserID | None = None,
        entity_type: AgentSessionEntity | None = None,
        entity_id: uuid.UUID | None = None,
        exclude_entity_types: list[AgentSessionEntity] | None = None,
        parent_session_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentSessionRead | ChatReadMinimal]:
        """List agent sessions and legacy chats for the workspace.

        Returns a merged list of AgentSession and legacy Chat records,
        sorted by created_at. Legacy chats have is_readonly=True.

        Args:
            created_by: Filter by user who created the session.
            entity_type: Filter by entity type.
            entity_id: Filter by entity ID.
            exclude_entity_types: Entity types to exclude from results.
            parent_session_id: Filter by parent session ID (for finding forked sessions).
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of AgentSessionRead or ChatReadMinimal (legacy, read-only).
        """
        # Query AgentSession table
        session_stmt = select(AgentSession).where(
            AgentSession.workspace_id == self.workspace_id
        )
        if created_by is not None:
            session_stmt = session_stmt.where(AgentSession.created_by == created_by)
        if entity_type is not None:
            session_stmt = session_stmt.where(
                AgentSession.entity_type == entity_type.value
            )
        if exclude_entity_types:
            session_stmt = session_stmt.where(
                AgentSession.entity_type.notin_(
                    [et.value for et in exclude_entity_types]
                )
            )
        if entity_id is not None:
            session_stmt = session_stmt.where(AgentSession.entity_id == entity_id)
        if parent_session_id is not None:
            session_stmt = session_stmt.where(
                AgentSession.parent_session_id == parent_session_id
            )

        session_result = await self.session.execute(session_stmt)
        sessions = list(session_result.scalars().all())

        # Query legacy Chat table
        # Note: exclude_entity_types is not applied here because legacy Chat records
        # predate entity types like WORKFLOW and APPROVAL that are typically excluded.
        # Legacy chats only have entity types like "case" or "agent_preset".
        chat_stmt = select(Chat).where(Chat.workspace_id == self.workspace_id)
        if created_by is not None:
            chat_stmt = chat_stmt.where(Chat.user_id == created_by)
        if entity_type is not None:
            chat_stmt = chat_stmt.where(Chat.entity_type == entity_type.value)
        if entity_id is not None:
            chat_stmt = chat_stmt.where(Chat.entity_id == entity_id)

        chat_result = await self.session.execute(chat_stmt)
        legacy_chats = list(chat_result.scalars().all())

        # Convert and merge
        items: list[AgentSessionRead | ChatReadMinimal] = []

        for s in sessions:
            items.append(AgentSessionRead.model_validate(s, from_attributes=True))

        for c in legacy_chats:
            # ChatReadMinimal has is_readonly=True by default
            items.append(ChatReadMinimal.model_validate(c, from_attributes=True))

        # Sort by created_at descending and apply pagination
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[offset : offset + limit]

    @audit_log(resource_type="agent_session", action="update")
    async def update_session(
        self,
        agent_session: AgentSession,
        *,
        params: AgentSessionUpdate,
    ) -> AgentSession:
        """Update an agent session.

        Args:
            agent_session: The AgentSession model to update.
            params: Fields to update.

        Returns:
            The updated AgentSession.
        """
        set_fields = params.model_dump(exclude_unset=True)

        if "agent_preset_id" in set_fields:
            preset_id = set_fields.pop("agent_preset_id")
            if preset_id is not None:
                preset_service = AgentPresetService(self.session, self.role)
                if not await preset_service.get_preset(preset_id):
                    raise TracecatNotFoundError(
                        f"Agent preset with ID '{preset_id}' not found"
                    )
            agent_session.agent_preset_id = preset_id

        # Update remaining fields if provided
        for field, value in set_fields.items():
            setattr(agent_session, field, value)
        self.session.add(agent_session)
        await self.session.commit()
        await self.session.refresh(agent_session)

        return agent_session

    @audit_log(resource_type="agent_session", action="delete")
    async def delete_session(
        self,
        agent_session: AgentSession,
    ) -> None:
        """Delete an agent session and its history.

        Args:
            agent_session: The AgentSession model to delete.
        """
        await self.session.delete(agent_session)
        await self.session.commit()

    async def update_last_stream_id(
        self,
        agent_session: AgentSession,
        last_stream_id: str,
    ) -> AgentSession:
        """Update the last stream ID for an agent session.

        Args:
            agent_session: The agent session to update.
            last_stream_id: The Redis stream ID to store.

        Returns:
            The updated AgentSession.
        """
        agent_session.last_stream_id = last_stream_id
        self.session.add(agent_session)
        await self.session.commit()
        await self.session.refresh(agent_session)
        return agent_session

    # =========================================================================
    # Session History Management (for Claude SDK session persistence)
    # =========================================================================

    async def load_session_history(
        self,
        session_id: uuid.UUID,
    ) -> SessionHistoryData | None:
        """Load session history for resume.

        Reconstructs the SDK session JSONL from stored history entries.
        Returns None if no history exists or no sdk_session_id is set.

        For forked sessions (with parent_session_id), loads the parent's history
        and sets is_fork=True so the runtime uses fork_session=True with the SDK.

        The sdk_session_id is stored on the AgentSession model (not in the
        JSONL content) to keep the history entries pristine for SDK resume.

        Args:
            session_id: The session UUID.

        Returns:
            SessionHistoryData with sdk_session_id and reconstructed JSONL,
            or None if no history found or sdk_session_id not set.
        """
        # First get the AgentSession to check for fork and retrieve sdk_session_id
        agent_session = await self.get_session(session_id)
        if agent_session is None:
            return None

        # For forked sessions, only fork on the first turn (when child has no sdk_session_id yet)
        # On subsequent turns, child has its own sdk_session_id and should resume normally
        if (
            agent_session.parent_session_id is not None
            and agent_session.sdk_session_id is None
        ):
            parent_session = await self.get_session(agent_session.parent_session_id)
            if parent_session is None:
                logger.warning(
                    "Forked session references non-existent parent",
                    session_id=session_id,
                    parent_session_id=agent_session.parent_session_id,
                )
                return None
            # Use parent's sdk_session_id and history
            source_session = parent_session
            source_session_id = agent_session.parent_session_id
        else:
            source_session = agent_session
            source_session_id = session_id

        sdk_session_id = source_session.sdk_session_id
        if not sdk_session_id:
            logger.debug(
                "No sdk_session_id on session (new session or legacy)",
                session_id=source_session_id,
            )
            return None

        # Load history entries from source session
        stmt = (
            select(AgentSessionHistory)
            .where(
                AgentSessionHistory.session_id == source_session_id,
            )
            .order_by(AgentSessionHistory.surrogate_id)
        )
        result = await self.session.execute(stmt)
        history_entries = list(result.scalars().all())

        if not history_entries:
            logger.warning(
                "sdk_session_id set but no history entries",
                session_id=source_session_id,
                sdk_session_id=sdk_session_id,
            )
            return None

        # Reconstruct JSONL from history entries (content stored pristine)
        lines = []
        for entry in history_entries:
            line = orjson.dumps(entry.content).decode("utf-8")
            lines.append(line)

        sdk_session_data = "\n".join(lines)

        return SessionHistoryData(
            sdk_session_id=sdk_session_id,
            sdk_session_data=sdk_session_data,
        )

    async def get_session_history(
        self,
        session_id: uuid.UUID,
    ) -> list[AgentSessionHistory]:
        """Get all history entries for a session.

        Args:
            session_id: The session UUID.

        Returns:
            List of AgentSessionHistory entries ordered by surrogate_id.
        """
        stmt = (
            select(AgentSessionHistory)
            .where(
                AgentSessionHistory.session_id == session_id,
            )
            .order_by(AgentSessionHistory.surrogate_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # =========================================================================
    # Chat / Message Turn Operations
    # =========================================================================

    async def run_turn(
        self,
        session_id: uuid.UUID,
        request: ChatRequest | ContinueRunRequest,
    ) -> ChatResponse | None:
        """Run a session turn by spawning a DurableAgentWorkflow.

        This method prepares the chat turn and spawns a DurableAgentWorkflow
        on the agent-action-queue for durable execution.

        Args:
            session_id: The ID of the session.
            request: Either a ChatRequest (start) or ContinueRunRequest (continue).

        Returns:
            ChatResponse if starting a new turn, None if continuing.

        Raises:
            TracecatNotFoundError: If the session is not found.
            ValueError: If the request/entity type is unsupported.
        """
        from tracecat_ee.agent.types import AgentWorkflowID
        from tracecat_ee.agent.workflows.durable import (
            AgentWorkflowArgs,
            DurableAgentWorkflow,
        )

        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        # Get the session
        agent_session = await self.get_session(session_id)
        if not agent_session:
            raise TracecatNotFoundError(f"Session with ID {session_id} not found")

        # Parse request to extract user prompt
        user_prompt: str | None = None
        is_continuation = False

        match request:
            case ContinueRunRequest():
                is_continuation = True
            case VercelChatRequest(message=ui_message):
                [message] = tracecat.agent.adapter.vercel.convert_ui_message(ui_message)
                match message:
                    case ModelRequest(parts=[UserPromptPart(content=content)]):
                        match content:
                            case str(s):
                                user_prompt = s
                            case list(l):
                                user_prompt = "\n".join(str(item) for item in l)
                            case _:
                                raise ValueError(f"Unsupported user prompt: {content}")
                    case _:
                        raise ValueError(f"Unsupported message: {message}")
            case BasicChatRequest(message=prompt):
                user_prompt = prompt
            case _:
                raise ValueError(f"Unsupported request type: {type(request)}")

        if user_prompt is not None:
            logger.info("Received user prompt", prompt_length=len(user_prompt))

        # Handle continuation (approval submission) vs new turn
        if is_continuation and isinstance(request, ContinueRunRequest):
            return await self._continue_with_approvals(session_id, request)

        # Build agent config and spawn workflow for new turn
        async with self._build_agent_config(agent_session) as agent_config:
            run_id = uuid.uuid4()

            # Copilot uses org-level credentials; other entities use workspace credentials
            use_workspace_credentials = (
                agent_session.entity_type != AgentSessionEntity.COPILOT
            )

            args = RunAgentArgs(
                user_prompt=user_prompt or "",
                session_id=session_id,
                config=agent_config,
                use_workspace_credentials=use_workspace_credentials,
            )

            client = await get_temporal_client()
            workflow_id = AgentWorkflowID(run_id)

            workflow_args = AgentWorkflowArgs(
                role=self.role,
                agent_args=args,
                title=agent_session.title,
                entity_type=AgentSessionEntity(agent_session.entity_type),
                entity_id=agent_session.entity_id,
                tools=agent_session.tools,
                agent_preset_id=agent_session.agent_preset_id,
            )

            # Update session with current run_id for approval lookups
            agent_session.curr_run_id = run_id
            self.session.add(agent_session)
            await self.session.commit()

            logger.info(
                "Spawning DurableAgentWorkflow",
                workflow_id=str(workflow_id),
                session_id=str(session_id),
                run_id=str(run_id),
                entity_type=agent_session.entity_type,
                entity_id=str(agent_session.entity_id)
                if agent_session.entity_id
                else None,
                task_queue=config.TRACECAT__AGENT_QUEUE,
            )

            await client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=str(workflow_id),
                task_queue=config.TRACECAT__AGENT_QUEUE,
                execution_timeout=timedelta(hours=1),
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            )

        # Return ChatResponse with session_id for streaming
        stream_url = f"/api/agent/sessions/{session_id}/stream"
        return ChatResponse(stream_url=stream_url, chat_id=session_id)

    async def _continue_with_approvals(
        self,
        session_id: uuid.UUID,
        request: ContinueRunRequest,
    ) -> None:
        """Continue an agent workflow by submitting approval decisions.

        Uses Temporal's workflow update mechanism to signal the waiting workflow
        with the approval decisions.

        Args:
            session_id: The ID of the agent session to continue.
            request: The continuation request containing approval decisions.

        Raises:
            TracecatNotFoundError: If no active session exists.
        """
        from tracecat_ee.agent.approvals.service import ApprovalMap
        from tracecat_ee.agent.types import AgentWorkflowID
        from tracecat_ee.agent.workflows.durable import (
            DurableAgentWorkflow,
            WorkflowApprovalSubmission,
        )

        agent_session = await self.get_session(session_id)
        if agent_session is None:
            raise TracecatNotFoundError(
                f"No active agent session found with ID {session_id}"
            )
        if agent_session.curr_run_id is None:
            raise TracecatNotFoundError(
                f"No active workflow run for session {session_id}"
            )

        # Build ApprovalMap from the request decisions
        approval_map: ApprovalMap = {}
        for decision in request.decisions:
            if decision.action == "approve":
                if decision.override_args:
                    approval_map[decision.tool_call_id] = ToolApproved(
                        override_args=decision.override_args
                    )
                else:
                    approval_map[decision.tool_call_id] = True
            else:
                approval_map[decision.tool_call_id] = ToolDenied(
                    message=decision.reason or "Tool denied by user"
                )

        # Get workflow handle using curr_run_id
        client = await get_temporal_client()
        workflow_id = AgentWorkflowID(agent_session.curr_run_id)

        logger.info(
            "Submitting approval decisions to workflow",
            workflow_id=str(workflow_id),
            session_id=str(agent_session.id),
            run_id=str(agent_session.curr_run_id),
            num_decisions=len(approval_map),
        )

        handle = client.get_workflow_handle_for(
            DurableAgentWorkflow.run,
            str(workflow_id),
        )

        await handle.execute_update(
            DurableAgentWorkflow.set_approvals,
            WorkflowApprovalSubmission(
                approvals=approval_map,
                approved_by=self.role.user_id,
            ),
        )

        logger.info(
            "Approval decisions submitted successfully",
            workflow_id=str(workflow_id),
            session_id=str(agent_session.id),
        )

        return None

    @contextlib.asynccontextmanager
    async def _build_agent_config(
        self, agent_session: AgentSession
    ) -> AsyncIterator[AgentConfig]:
        """Build agent configuration for a session based on its entity type.

        Args:
            agent_session: The session to build config for.

        Yields:
            AgentConfig: The configured agent config.

        Raises:
            ValueError: If the session entity type is unsupported.
            TracecatNotFoundError: If required resources are not found.
        """
        agent_svc = AgentManagementService(self.session, self.role)

        if agent_session.entity_type is None:
            # No entity type - use default config with workspace credentials
            async with agent_svc.with_model_config(
                use_workspace_credentials=True
            ) as model_config:
                yield AgentConfig(
                    instructions="",
                    model_name=model_config.name,
                    model_provider=model_config.provider,
                    actions=agent_session.tools,
                )
            return

        session_entity = AgentSessionEntity(agent_session.entity_type)

        if session_entity is AgentSessionEntity.CASE:
            entity_instructions = await self._entity_to_prompt(agent_session)
            if agent_session.agent_preset_id:
                async with agent_svc.with_preset_config(
                    preset_id=agent_session.agent_preset_id
                ) as preset_config:
                    combined_instructions = (
                        f"{preset_config.instructions}\n\n{entity_instructions}"
                        if preset_config.instructions
                        else entity_instructions
                    )
                    config = replace(preset_config, instructions=combined_instructions)
                    yield config
            else:
                # Case chat without preset uses workspace credentials
                async with agent_svc.with_model_config(
                    use_workspace_credentials=True
                ) as model_config:
                    yield AgentConfig(
                        instructions=entity_instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=agent_session.tools,
                    )
        elif session_entity is AgentSessionEntity.AGENT_PRESET:
            # Live chat uses workspace-level credentials
            async with agent_svc.with_preset_config(
                preset_id=agent_session.entity_id, use_workspace_credentials=True
            ) as preset_config:
                yield preset_config
        elif session_entity is AgentSessionEntity.AGENT_PRESET_BUILDER:
            if agent_session.entity_id is None:
                raise ValueError("Agent preset builder requires entity_id")
            instructions = await self._entity_to_prompt(agent_session)
            try:
                # Agent preset builder uses workspace credentials
                # Tools are resolved via MCP path in the durable workflow
                # (internal tools + bundled registry actions)
                async with agent_svc.with_model_config(
                    use_workspace_credentials=True
                ) as model_config:
                    yield AgentConfig(
                        instructions=instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=None,
                    )
            except TracecatNotFoundError as exc:
                raise ValueError(
                    "Agent preset builder requires a default AI model with valid provider credentials. "
                    "Configure credentials in Workspace settings before chatting."
                ) from exc
        elif session_entity is AgentSessionEntity.COPILOT:
            # Copilot uses org-level credentials, not workspace credentials
            entity_instructions = await self._entity_to_prompt(agent_session)
            if agent_session.agent_preset_id:
                async with agent_svc.with_preset_config(
                    preset_id=agent_session.agent_preset_id,
                    use_workspace_credentials=False,
                ) as preset_config:
                    combined_instructions = (
                        f"{preset_config.instructions}\n\n{entity_instructions}"
                        if preset_config.instructions
                        else entity_instructions
                    )
                    config = replace(preset_config, instructions=combined_instructions)
                    yield config
            else:
                # Copilot without preset uses org-level credentials (default)
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions=entity_instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=agent_session.tools,
                    )
        elif session_entity in (
            AgentSessionEntity.WORKFLOW,
            AgentSessionEntity.APPROVAL,
        ):
            # Check if this is a forked session (has parent_session_id)
            if agent_session.parent_session_id is not None:
                # Forked sessions use the parent's preset but with no tools.
                # Prepend context - agent should not mention this is a forked session.
                fork_context = (
                    "You do not have access to any tools in this conversation. "
                    "If the user asks you to perform actions, politely decline and "
                    "suggest they start a new workflow if they need to take action.\n\n"
                )
                # Get parent session to check for preset
                parent_session = await self.get_session(agent_session.parent_session_id)
                if parent_session and parent_session.agent_preset_id:
                    # Use parent's preset with forked context prepended
                    async with agent_svc.with_preset_config(
                        preset_id=parent_session.agent_preset_id,
                        use_workspace_credentials=True,
                    ) as preset_config:
                        combined_instructions = (
                            f"{fork_context}{preset_config.instructions}"
                            if preset_config.instructions
                            else fork_context.strip()
                        )
                        yield AgentConfig(
                            instructions=combined_instructions,
                            model_name=preset_config.model_name,
                            model_provider=preset_config.model_provider,
                            actions=[],  # No tools for forked sessions
                        )
                else:
                    # No preset - use workspace model with fork context
                    async with agent_svc.with_model_config(
                        use_workspace_credentials=True
                    ) as model_config:
                        yield AgentConfig(
                            instructions=fork_context.strip(),
                            model_name=model_config.name,
                            model_provider=model_config.provider,
                            actions=[],  # No tools for forked sessions
                        )
            elif agent_session.agent_preset_id:
                # Workflow sessions with preset use the preset config
                async with agent_svc.with_preset_config(
                    preset_id=agent_session.agent_preset_id,
                    use_workspace_credentials=True,
                ) as preset_config:
                    yield preset_config
            else:
                # Workflow without preset uses workspace credentials
                async with agent_svc.with_model_config(
                    use_workspace_credentials=True
                ) as model_config:
                    yield AgentConfig(
                        instructions="",
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        actions=agent_session.tools,
                    )
        else:
            raise ValueError(
                f"Unsupported session entity type: {agent_session.entity_type}. "
                f"Expected one of: {list(AgentSessionEntity)}"
            )

    async def _entity_to_prompt(self, agent_session: AgentSession) -> str:
        """Get the prompt for a given entity type."""
        entity_type = agent_session.entity_type
        entity_id = agent_session.entity_id

        if entity_type == AgentSessionEntity.CASE:
            cases_service = CasesService(self.session, self.role)
            case = await cases_service.get_case(entity_id)
            if not case:
                raise TracecatNotFoundError(f"Case with ID {entity_id} not found")
            return CaseCopilotPrompts(case=case).instructions
        if entity_type == AgentSessionEntity.AGENT_PRESET_BUILDER:
            agent_preset_service = AgentPresetService(self.session, self.role)
            if not (preset := await agent_preset_service.get_preset(entity_id)):
                raise TracecatNotFoundError(
                    f"Agent preset with ID '{entity_id}' not found"
                )
            prompt = AgentPresetBuilderPrompt(preset=preset)
            return prompt.instructions
        if entity_type == AgentSessionEntity.COPILOT:
            return WorkspaceCopilotPrompts().instructions
        else:
            raise ValueError(
                f"Unsupported session entity type: {entity_type}. "
                f"Expected one of: {list(AgentSessionEntity)}"
            )

    # =========================================================================
    # Message Retrieval
    # =========================================================================

    async def list_messages(
        self,
        session_id: uuid.UUID,
        *,
        kinds: Sequence[MessageKind] | None = None,
    ) -> list[ChatMessage]:
        """Retrieve session messages, optionally filtered by message kind.

        For forked sessions, includes parent session messages first.
        Checks the new AgentSessionHistory table first, then falls back to
        the legacy ChatMessage table for backward compatibility.

        Args:
            session_id: The session UUID (could be AgentSession.id or Chat.id).
            kinds: Optional list of message kinds to filter by.

        Returns:
            List of ChatMessage objects (parent messages + current if forked).
        """
        agent_session = await self.get_session(session_id)

        # If no history in new table, fall back to legacy ChatMessage table
        if not agent_session:
            chat_service = ChatService(self.session, self.role)
            return await chat_service.list_legacy_messages(session_id, kinds=kinds)

        session_ids = [session_id]
        if agent_session and agent_session.parent_session_id:
            session_ids.insert(0, agent_session.parent_session_id)

        # Fetch all history entries (both chat-message and internal)
        # Internal entries are needed for tool result enrichment in the adapter
        all_history_stmt = (
            select(AgentSessionHistory)
            .where(AgentSessionHistory.session_id.in_(session_ids))
            .order_by(AgentSessionHistory.surrogate_id)
        )
        all_history_result = await self.session.execute(all_history_stmt)
        all_entries = list(all_history_result.scalars().all())

        # Fetch approvals for this session and parent session (for forked sessions)
        approval_stmt = select(Approval).where(Approval.session_id.in_(session_ids))
        approval_result = await self.session.execute(approval_stmt)
        approvals = approval_result.scalars().all()
        approval_by_tool_id: dict[str, Approval] = {
            a.tool_call_id: a for a in approvals
        }

        # Build timeline with interleaved approvals
        # Process both chat-message and internal entries in order
        # Internal entries contain tool results that the adapter will extract
        messages: list[ChatMessage] = []
        for entry in all_entries:
            content = entry.content
            if not content:
                continue

            # Skip internal entries (e.g., continuation prompts)
            if entry.kind == MessageKind.INTERNAL.value:
                continue

            # Skip non-message entries (e.g., system metadata)
            msg_type = content.get("type")
            if msg_type not in ("user", "assistant"):
                continue

            # All AgentSessionHistory entries are CHAT_MESSAGE kind
            kind = MessageKind.CHAT_MESSAGE

            # Filter by kinds if specified
            if kinds and kind not in kinds:
                continue

            # Extract the inner message from JSONL envelope
            inner_message = content.get("message")
            if not inner_message:
                inner_message = content

            # Deserialize the content using Claude SDK TypeAdapter
            message = ClaudeSDKMessageTA.validate_python(inner_message)
            messages.append(ChatMessage(id=str(entry.id), message=message))

            # For assistant messages, check for tool calls needing approval bubbles
            if msg_type == "assistant":
                tool_uses = self._extract_tool_uses_from_message(inner_message)
                for tool_use in tool_uses:
                    tool_use_id = tool_use.get("id")
                    if tool_use_id and (
                        approval := approval_by_tool_id.get(tool_use_id)
                    ):
                        # Insert approval-request bubble
                        approval_read = ApprovalRead.model_validate(approval)
                        messages.append(
                            ChatMessage(
                                id=str(approval.id),
                                kind=MessageKind.APPROVAL_REQUEST,
                                approval=approval_read,
                            )
                        )
                        # If decided, also insert decision bubble
                        if approval.status != ApprovalStatus.PENDING:
                            messages.append(
                                ChatMessage(
                                    id=f"{approval.id}-decision",
                                    kind=MessageKind.APPROVAL_DECISION,
                                    approval=approval_read,
                                )
                            )

        return messages

    @staticmethod
    def _extract_tool_uses_from_message(
        message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract tool_use blocks from a Claude SDK message."""
        content = message.get("content", [])
        if isinstance(content, str):
            return []
        return [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]

    # =========================================================================
    # Approval Flow: Replace Interrupt Entries
    # =========================================================================

    async def replace_interrupt_with_tool_results(
        self,
        session_id: uuid.UUID,
        tool_results: Sequence[ToolExecutionResult],
    ) -> None:
        """Replace interrupt entries with proper tool_result entry.

        After approval execution, the session history contains SDK-generated
        interrupt entries (error tool_result, interrupt text, synthetic message).
        This method:
        1. Finds the assistant message with tool_use blocks (for parentUuid)
        2. Deletes the interrupt entries
        3. Inserts a proper tool_result JSONL entry

        Args:
            session_id: The session UUID.
            tool_results: Tool execution results (both approved and denied).
        """
        if not tool_results:
            return

        session = await self.get_session(session_id)
        if not session:
            raise TracecatNotFoundError(f"Session {session_id} not found")
        if not session.sdk_session_id:
            raise ValueError(f"Session {session_id} has no sdk_session_id")

        tool_call_ids = {tr.tool_call_id for tr in tool_results}

        # Find the assistant message containing these tool_uses (for parentUuid)
        history = await self.get_session_history(session_id)
        assistant_uuid = None
        assistant_surrogate_id = None

        for entry in reversed(history):
            if entry.content.get("type") == "assistant":
                tool_uses = self._extract_tool_uses_from_message(
                    entry.content.get("message", {})
                )
                if any(tu.get("id") in tool_call_ids for tu in tool_uses):
                    assistant_uuid = entry.content.get("uuid")
                    assistant_surrogate_id = entry.surrogate_id
                    break

        if assistant_uuid is None:
            logger.warning(
                "Could not find assistant message with tool_use for replacement",
                session_id=session_id,
                tool_call_ids=tool_call_ids,
            )
            return
        if assistant_surrogate_id is None:
            logger.warning(
                "Could not find assistant message with tool_use for replacement",
                session_id=session_id,
                tool_call_ids=tool_call_ids,
            )
            return
        # Delete interrupt entries that follow the assistant message
        await self._delete_interrupt_entries_for_tool_calls(
            session_id, assistant_surrogate_id, tool_call_ids
        )

        # Build and insert proper tool_result entry
        entry_content = {
            "uuid": str(uuid.uuid4()),
            "parentUuid": assistant_uuid,
            "sessionId": session.sdk_session_id,
            "type": "user",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "cwd": "/home/agent",
            "version": "2.0.72",
            "userType": "external",
            "gitBranch": "",
            "isSidechain": False,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": self._serialize_tool_result(tr.result),
                        "is_error": tr.is_error,
                    }
                    for tr in tool_results
                ],
            },
        }

        history_entry = AgentSessionHistory(
            session_id=session_id,
            workspace_id=self.workspace_id,
            content=entry_content,
            kind="chat-message",
        )
        self.session.add(history_entry)
        await self.session.commit()

        logger.info(
            "Replaced interrupt entries with proper tool_result",
            session_id=session_id,
            tool_call_ids=list(tool_call_ids),
            parent_uuid=assistant_uuid,
        )

    async def _delete_interrupt_entries_for_tool_calls(
        self,
        session_id: uuid.UUID,
        assistant_surrogate_id: int,
        tool_call_ids: set[str],
    ) -> None:
        """Delete interrupt entries that follow the assistant tool_use message.

        Entries to delete:
        1. Error tool_result: type="user", content has tool_result with is_error=true
        2. Interrupt text: type="user", content contains "[Request interrupted"
        3. Synthetic: type="assistant", message.model="<synthetic>"

        Args:
            session_id: The session UUID.
            assistant_surrogate_id: The surrogate_id of the assistant message with tool_use.
            tool_call_ids: Set of tool_call_ids we're replacing.
        """
        # Get entries after the assistant message
        stmt = (
            select(AgentSessionHistory)
            .where(
                AgentSessionHistory.session_id == session_id,
                AgentSessionHistory.surrogate_id > assistant_surrogate_id,
            )
            .order_by(AgentSessionHistory.surrogate_id)
        )
        result = await self.session.execute(stmt)
        entries = list(result.scalars().all())

        entries_to_delete: list[AgentSessionHistory] = []

        for entry in entries:
            content = entry.content
            if not content:
                continue

            msg_type = content.get("type")
            message = content.get("message", {})
            msg_content = message.get("content", [])

            # Check for error tool_result or interrupt text in user messages
            if msg_type == "user" and isinstance(msg_content, list):
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue
                    # Check for error tool_result matching our tool_call_ids
                    if (
                        block.get("type") == "tool_result"
                        and block.get("is_error") is True
                        and block.get("tool_use_id") in tool_call_ids
                    ):
                        entries_to_delete.append(entry)
                        break
                    # Check for interrupt text
                    if block.get(
                        "type"
                    ) == "text" and "[Request interrupted" in block.get("text", ""):
                        entries_to_delete.append(entry)
                        break

            # Check for synthetic assistant message
            elif msg_type == "assistant" and message.get("model") == "<synthetic>":
                entries_to_delete.append(entry)

        # Delete the identified entries
        if entries_to_delete:
            ids_to_delete = [e.id for e in entries_to_delete]
            delete_stmt = delete(AgentSessionHistory).where(
                AgentSessionHistory.id.in_(ids_to_delete)
            )
            await self.session.execute(delete_stmt)

            logger.info(
                "Deleted interrupt entries",
                session_id=session_id,
                deleted_count=len(entries_to_delete),
                deleted_ids=[str(id) for id in ids_to_delete],
            )

    @staticmethod
    def _serialize_tool_result(result: Any) -> str:
        """Serialize a tool result to string for Claude SDK format."""
        if isinstance(result, str):
            return result
        try:
            return orjson.dumps(result).decode("utf-8")
        except (TypeError, ValueError):
            return str(result)

    # =========================================================================
    # Session Forking (for post-decision agent interactions)
    # =========================================================================

    async def fork_session(
        self,
        parent_session_id: uuid.UUID,
        *,
        entity_type: AgentSessionEntity | None = None,
    ) -> AgentSession:
        """Create a forked session from a parent session.

        Forked sessions allow users to continue interacting with an agent
        after making approval decisions, to ask for context or clarification.

        Args:
            parent_session_id: The ID of the session to fork.
            entity_type: Override entity type for the forked session. If None,
                inherits from parent. Use APPROVAL for inbox forks to hide
                from main chat list.

        Returns:
            The newly created forked AgentSession.

        Raises:
            TracecatNotFoundError: If the parent session is not found.
        """
        parent = await self.get_session(parent_session_id)
        if parent is None:
            raise TracecatNotFoundError(
                f"Parent session with ID {parent_session_id} not found"
            )

        # Forked sessions are read-only "reviewer" sessions.
        forked_session = AgentSession(
            workspace_id=self.workspace_id,
            # Metadata - inherit from parent, except entity_type if overridden
            title=f"{parent.title} (continued)",
            created_by=self.role.user_id,
            entity_type=entity_type.value if entity_type else parent.entity_type,
            entity_id=parent.entity_id,
            tools=[],
            agent_preset_id=None,
            # Harness - inherit from parent
            harness_type=parent.harness_type,
            # Fork reference
            parent_session_id=parent_session_id,
        )
        self.session.add(forked_session)
        await self.session.commit()
        await self.session.refresh(forked_session)

        logger.info(
            "Created forked session",
            forked_session_id=forked_session.id,
            parent_session_id=parent_session_id,
        )

        return forked_session
