"""Temporal activities for agent session management.

These activities handle:
- Loading session history from DB for runtime resume
- Persisting session history from runtime for durability (atomic with chat messages)
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from temporalio import activity

from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.logger import logger


class CreateSessionInput(BaseModel):
    """Input for create_session_activity."""

    role: Role
    session_id: uuid.UUID
    # Entity context
    entity_type: AgentSessionEntity
    entity_id: uuid.UUID
    # Session config
    title: str = "New Chat"
    created_by: uuid.UUID | None = None
    tools: list[str] | None = None
    agent_preset_id: uuid.UUID | None = None
    harness_type: HarnessType = HarnessType.CLAUDE_CODE
    # Workflow run tracking (for approval lookups)
    curr_run_id: uuid.UUID | None = None
    # First user prompt for best-effort auto-title
    initial_user_prompt: str | None = None


class CreateSessionResult(BaseModel):
    """Result from create_session_activity."""

    session_id: uuid.UUID
    success: bool
    error: str | None = None


class LoadSessionInput(BaseModel):
    """Input for load_session_activity."""

    role: Role
    session_id: uuid.UUID


class LoadSessionResult(BaseModel):
    """Result from load_session_activity."""

    found: bool
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None
    is_fork: bool = False  # If True, runtime should use fork_session=True with SDK
    error: str | None = None


@activity.defn
async def create_session_activity(input: CreateSessionInput) -> CreateSessionResult:
    """Create or get an existing agent session in the database.

    This is called at the start of an agent workflow to establish
    the session record that will track the agent's execution.
    Idempotent - safe to call multiple times for the same session_id.

    If curr_run_id is provided, it will be set on the session to enable
    approval submission to locate the running workflow.
    """
    ctx_role.set(input.role)

    try:
        async with AgentSessionService.with_session(role=input.role) as service:
            agent_session, created = await service.get_or_create_session(
                AgentSessionCreate(
                    id=input.session_id,
                    title=input.title,
                    created_by=input.created_by,
                    entity_type=input.entity_type,
                    entity_id=input.entity_id,
                    tools=input.tools,
                    agent_preset_id=input.agent_preset_id,
                    harness_type=input.harness_type,
                )
            )

            # Set curr_run_id if provided (for workflow-initiated sessions)
            if input.curr_run_id is not None:
                agent_session.curr_run_id = input.curr_run_id
                service.session.add(agent_session)
                await service.session.commit()

            if input.initial_user_prompt is not None:
                await service.auto_title_session_on_first_prompt(
                    agent_session,
                    input.initial_user_prompt,
                )

        if created:
            logger.info(
                "Created agent session",
                session_id=input.session_id,
                entity_type=input.entity_type,
                entity_id=input.entity_id,
            )
        else:
            logger.debug(
                "Agent session already exists",
                session_id=input.session_id,
            )

        return CreateSessionResult(session_id=input.session_id, success=True)

    except Exception as e:
        logger.error("Failed to create agent session", error=str(e))
        return CreateSessionResult(
            session_id=input.session_id, success=False, error=str(e)
        )


@activity.defn
async def load_session_activity(input: LoadSessionInput) -> LoadSessionResult:
    """Load agent session history for resume.

    Retrieves the stored SDK session data (JSONL) so the runtime
    can resume from where it left off.
    """
    ctx_role.set(input.role)

    try:
        async with AgentSessionService.with_session(role=input.role) as service:
            agent_session = await service.get_session(input.session_id)

            if agent_session is None:
                return LoadSessionResult(found=False)

            # Load the session history
            history = await service.load_session_history(input.session_id)

            if history is None:
                return LoadSessionResult(
                    found=True,
                    sdk_session_id=None,
                    sdk_session_data=None,
                )

            return LoadSessionResult(
                found=True,
                sdk_session_id=history.sdk_session_id,
                sdk_session_data=history.sdk_session_data,
                is_fork=history.is_fork,
            )

    except Exception as e:
        logger.error("Failed to load agent session", error=str(e))
        return LoadSessionResult(found=False, error=str(e))


def get_session_activities() -> list:
    """Get all session-related activities for worker registration."""
    return [
        create_session_activity,
        load_session_activity,
    ]
