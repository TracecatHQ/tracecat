"""Dispatch case-comment agent invocations into agent sessions."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.cases.agent_invocations.prompts import build_comment_agent_input
from tracecat.cases.agent_invocations.service import (
    CaseCommentAgentInvocationService,
)
from tracecat.cases.agent_invocations.types import PreparedCommentAgentSession
from tracecat.cases.agent_sessions.service import (
    CaseAgentSessionInteractionService,
)
from tracecat.cases.enums import (
    CaseAgentSessionInteractionOperation,
    CaseCommentAgentInvocationStatus,
    MentionTargetType,
)
from tracecat.db.models import CaseCommentAgentInvocation, CaseCommentMention
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService

COMMENT_AGENT_SESSION_CONTEXT = {"session_origin": "case_comment"}


class CaseCommentAgentInvocationDispatcher(BaseWorkspaceService):
    """Create linked agent sessions for comment invocation workflows."""

    service_name = "case_comment_agent_invocation_dispatcher"

    async def create_or_get_agent_session(
        self, invocation_id: uuid.UUID
    ) -> PreparedCommentAgentSession | None:
        """Create or recover the linked session without starting its turn."""
        invocation_service = CaseCommentAgentInvocationService(self.session, self.role)
        invocation = await invocation_service.claim_pending(invocation_id)
        if invocation is None:
            invocation = await self.session.scalar(
                select(CaseCommentAgentInvocation).where(
                    CaseCommentAgentInvocation.id == invocation_id,
                    CaseCommentAgentInvocation.workspace_id == self.workspace_id,
                    CaseCommentAgentInvocation.status
                    == CaseCommentAgentInvocationStatus.RUNNING.value,
                )
            )
        if invocation is None:
            return None

        target_statement = select(
            CaseCommentMention.case_id,
            CaseCommentMention.target_id,
        ).where(
            CaseCommentMention.id == invocation.mention_id,
            CaseCommentMention.workspace_id == self.workspace_id,
            CaseCommentMention.target_type == MentionTargetType.AGENT.value,
        )
        target = (await self.session.execute(target_statement)).tuples().one_or_none()
        if target is None:
            raise TracecatNotFoundError("Agent invocation mention not found")
        case_id, preset_id = target

        thread_context = await invocation_service.load_thread_context(invocation.id)
        if thread_context is None:
            raise TracecatNotFoundError("Agent invocation comment thread not found")

        agent_input = build_comment_agent_input(thread_context)

        session_service = AgentSessionService(self.session, self.role)
        if invocation.session_id is not None:
            agent_session = await session_service.get_session(invocation.session_id)
            if agent_session is None:
                raise TracecatNotFoundError("Linked agent session not found")
        else:
            preset_service = AgentPresetService(
                self.session, session_service.execution_role
            )
            preset_version = await preset_service.resolve_agent_preset_version(
                preset_id=preset_id
            )
            session_id = uuid.uuid4()
            invocation.session_id = session_id
            # Suppress query-triggered autoflush until create_session adds the row
            # referenced by invocation.session_id; its commit persists both together.
            with self.session.no_autoflush:
                agent_session = await session_service.create_session(
                    AgentSessionCreate(
                        id=session_id,
                        title=invocation.preset_name,
                        entity_type=AgentSessionEntity.CASE,
                        entity_id=case_id,
                        agent_preset_id=preset_id,
                        agent_preset_version_id=preset_version.id,
                    ),
                    channel_context=COMMENT_AGENT_SESSION_CONTEXT,
                )

        await CaseAgentSessionInteractionService(self.session, self.role).record(
            case_id=case_id,
            agent_session_id=agent_session.id,
            operation=CaseAgentSessionInteractionOperation.UPDATE,
        )
        await self.session.commit()
        await session_service.ensure_display_only_user_messages(
            agent_session.id,
            agent_input.display_messages,
        )
        return PreparedCommentAgentSession(
            invocation_id=invocation.id,
            session_id=agent_session.id,
            prompt=agent_input.model_context_prompt,
            display_messages=agent_input.display_messages,
        )
