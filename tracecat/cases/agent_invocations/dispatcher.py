"""Dispatch case-comment agent invocations into agent sessions."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.cases.agent_invocations.prompts import build_comment_agent_prompt
from tracecat.cases.agent_invocations.service import (
    CaseCommentAgentInvocationService,
)
from tracecat.cases.agent_invocations.types import PreparedCommentAgentSession
from tracecat.cases.enums import CaseCommentAgentInvocationStatus, MentionTargetType
from tracecat.db.models import CaseCommentAgentInvocation, CaseCommentMention
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService


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

        prompt = build_comment_agent_prompt(thread_context)

        session_service = AgentSessionService(self.session, self.role)
        if invocation.session_id is not None:
            agent_session = await session_service.get_session(invocation.session_id)
            if agent_session is None:
                raise TracecatNotFoundError("Linked agent session not found")
            return PreparedCommentAgentSession(
                invocation_id=invocation.id,
                session_id=agent_session.id,
                prompt=prompt,
            )

        preset_service = AgentPresetService(self.session, self.role)
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
                    title=f"{invocation.preset_name} case comment",
                    entity_type=AgentSessionEntity.CASE,
                    entity_id=case_id,
                    agent_preset_id=preset_id,
                    agent_preset_version_id=preset_version.id,
                )
            )
        return PreparedCommentAgentSession(
            invocation_id=invocation.id,
            session_id=agent_session.id,
            prompt=prompt,
        )
