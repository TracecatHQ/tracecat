"""Complete case-comment agent invocations with reply comments."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from tracecat.cases.agent_invocations.output import render_agent_output_as_comment
from tracecat.cases.agent_invocations.service import CaseCommentAgentInvocationService
from tracecat.cases.enums import CaseCommentAgentInvocationStatus, MentionTargetType
from tracecat.cases.schemas import CommentReplyCreatedEvent
from tracecat.cases.service import CaseEventsService
from tracecat.db.models import (
    Case,
    CaseComment,
    CaseCommentAgentInvocation,
    CaseCommentMention,
)
from tracecat.exceptions import TracecatConflictError
from tracecat.service import BaseWorkspaceService


class CaseCommentAgentInvocationCompletionService(BaseWorkspaceService):
    """Post agent replies and complete their originating invocations.

    This orchestration stays separate from ``CaseCommentAgentInvocationService``
    because it also depends on ``CaseEventsService`` from ``tracecat.cases.service``.
    That module already imports the invocation service, so moving this operation
    there would create a circular import.
    """

    service_name = "case_comment_agent_invocation_completion"

    async def create_reply_and_mark_succeeded(
        self,
        session_id: uuid.UUID,
        output: object,
    ) -> CaseComment | None:
        """Create one reply and atomically complete the linked invocation."""
        statement = (
            select(CaseCommentAgentInvocation, CaseComment, Case)
            .join(
                CaseCommentMention,
                CaseCommentMention.id == CaseCommentAgentInvocation.mention_id,
            )
            .join(CaseComment, CaseComment.id == CaseCommentMention.comment_id)
            .join(Case, Case.id == CaseComment.case_id)
            .where(
                CaseCommentAgentInvocation.session_id == session_id,
                CaseCommentAgentInvocation.workspace_id == self.workspace_id,
                CaseCommentMention.workspace_id == self.workspace_id,
                CaseCommentMention.target_type == MentionTargetType.AGENT.value,
                CaseComment.workspace_id == self.workspace_id,
                Case.workspace_id == self.workspace_id,
            )
            .with_for_update(of=CaseCommentAgentInvocation)
        )
        row = (await self.session.execute(statement)).tuples().one_or_none()
        if row is None:
            return None

        invocation, source_comment, case = row
        if invocation.status == CaseCommentAgentInvocationStatus.SUCCEEDED.value:
            if invocation.reply_comment_id is None:
                raise TracecatConflictError(
                    "Succeeded comment agent invocation has no reply"
                )
            return await self.session.scalar(
                select(CaseComment).where(
                    CaseComment.id == invocation.reply_comment_id,
                    CaseComment.workspace_id == self.workspace_id,
                )
            )
        if invocation.status != CaseCommentAgentInvocationStatus.RUNNING.value:
            return None

        content = render_agent_output_as_comment(output)
        thread_root_id = source_comment.parent_id or source_comment.id
        reply = CaseComment(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            case_id=case.id,
            content=content,
            parent_id=thread_root_id,
            user_id=None,
        )
        self.session.add(reply)
        await self.session.flush()
        await CaseEventsService(self.session, self.role).create_event(
            case,
            CommentReplyCreatedEvent(
                comment_id=reply.id,
                parent_id=thread_root_id,
                thread_root_id=thread_root_id,
            ),
        )
        transitioned = await CaseCommentAgentInvocationService(
            self.session, self.role
        ).mark_succeeded(invocation.id, reply.id)
        if transitioned is None:
            raise TracecatConflictError(
                "Comment agent invocation changed while creating its reply"
            )

        await self.session.commit()
        return reply
