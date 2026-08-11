"""Service for case-comment agent invocations."""

from __future__ import annotations

import uuid
from typing import cast

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import aliased

from tracecat.cases.agent_invocations.types import (
    CommentThreadContext,
    CommentThreadEntry,
    PersistedCommentAgentRole,
)
from tracecat.cases.enums import (
    CaseCommentAgentInvocationStatus,
    MentionTargetType,
)
from tracecat.db.models import (
    AgentPreset,
    CaseComment,
    CaseCommentAgentInvocation,
    CaseCommentMention,
    User,
)
from tracecat.service import BaseWorkspaceService

_DELETED_COMMENT_CONTENT = "Comment deleted"
_MAX_THREAD_COMMENTS = 20
_MAX_THREAD_COMMENT_LENGTH = 4_000
_TRUNCATED_THREAD_COMMENT_SUFFIX = "\n\n[Comment truncated]"


def _author_label(
    workflow_title: str | None,
    user: User | None,
    agent_preset_name: str | None,
) -> str:
    if agent_preset_name is not None:
        return f"Agent: {agent_preset_name}"
    if workflow_title is not None:
        return f"Workflow: {workflow_title}"
    if user is None:
        return "Tracecat"
    display_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return display_name or user.email


class CaseCommentAgentInvocationService(BaseWorkspaceService):
    """Manage agent invocations originating from case-comment mentions."""

    service_name = "case_comment_agent_invocations"

    async def claim_pending(
        self, invocation_id: uuid.UUID
    ) -> CaseCommentAgentInvocation | None:
        """Atomically claim a pending invocation for dispatch.

        The caller owns the transaction. A ``None`` result means the invocation
        was not pending in this workspace.
        """
        statement = (
            update(CaseCommentAgentInvocation)
            .where(
                CaseCommentAgentInvocation.id == invocation_id,
                CaseCommentAgentInvocation.workspace_id == self.workspace_id,
                CaseCommentAgentInvocation.status
                == CaseCommentAgentInvocationStatus.PENDING.value,
            )
            .values(
                status=CaseCommentAgentInvocationStatus.RUNNING.value,
                error=None,
            )
            .returning(CaseCommentAgentInvocation)
        )
        return await self.session.scalar(statement)

    async def mark_failed(
        self,
        invocation_id: uuid.UUID,
        error: str,
    ) -> CaseCommentAgentInvocation | None:
        """Atomically mark a running invocation as failed.

        The caller owns the transaction. A ``None`` result means the invocation
        was not running in this workspace.
        """
        statement = (
            update(CaseCommentAgentInvocation)
            .where(
                CaseCommentAgentInvocation.id == invocation_id,
                CaseCommentAgentInvocation.workspace_id == self.workspace_id,
                CaseCommentAgentInvocation.status
                == CaseCommentAgentInvocationStatus.RUNNING.value,
            )
            .values(
                status=CaseCommentAgentInvocationStatus.FAILED.value,
                error=error,
            )
            .returning(CaseCommentAgentInvocation)
        )
        return await self.session.scalar(statement)

    async def mark_succeeded(
        self,
        invocation_id: uuid.UUID,
        reply_comment_id: uuid.UUID,
    ) -> CaseCommentAgentInvocation | None:
        """Atomically mark a running invocation as succeeded with its reply.

        The caller owns the transaction. A ``None`` result means the invocation
        was not running in this workspace.
        """
        statement = (
            update(CaseCommentAgentInvocation)
            .where(
                CaseCommentAgentInvocation.id == invocation_id,
                CaseCommentAgentInvocation.workspace_id == self.workspace_id,
                CaseCommentAgentInvocation.status
                == CaseCommentAgentInvocationStatus.RUNNING.value,
            )
            .values(
                status=CaseCommentAgentInvocationStatus.SUCCEEDED.value,
                reply_comment_id=reply_comment_id,
                error=None,
            )
            .returning(CaseCommentAgentInvocation)
        )
        return await self.session.scalar(statement)

    async def create_pending_for_comment(
        self, comment_id: uuid.UUID
    ) -> list[CaseCommentAgentInvocation]:
        """Create pending invocations for persisted agent mentions.

        The caller owns the transaction. Existing invocation rows are ignored so
        retrying this operation cannot create duplicate work for a mention.

        Args:
            comment_id: Comment whose persisted mentions should be materialized.

        Returns:
            Newly created invocation rows.
        """
        persisted_role = cast(
            PersistedCommentAgentRole,
            self.role.model_dump(mode="json"),
        )
        source = (
            select(
                sa.func.gen_random_uuid(),
                CaseCommentMention.id,
                AgentPreset.name,
                AgentPreset.slug,
                sa.literal(persisted_role, type_=JSONB),
                sa.literal(CaseCommentAgentInvocationStatus.PENDING.value),
                sa.literal(self.workspace_id),
            )
            .join(AgentPreset, AgentPreset.id == CaseCommentMention.target_id)
            .where(
                CaseCommentMention.workspace_id == self.workspace_id,
                CaseCommentMention.comment_id == comment_id,
                CaseCommentMention.target_type == MentionTargetType.AGENT.value,
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
        )
        statement = (
            insert(CaseCommentAgentInvocation)
            .from_select(
                [
                    "id",
                    "mention_id",
                    "preset_name",
                    "preset_slug",
                    "role",
                    "status",
                    "workspace_id",
                ],
                source,
            )
            .on_conflict_do_nothing(index_elements=["mention_id"])
            .returning(CaseCommentAgentInvocation)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def load_thread_context(
        self, invocation_id: uuid.UUID
    ) -> CommentThreadContext | None:
        """Load the ordered comment thread containing an invocation."""
        source_statement = (
            select(
                CaseCommentMention.comment_id,
                CaseComment.case_id,
                CaseComment.parent_id,
            )
            .join(
                CaseCommentAgentInvocation,
                CaseCommentAgentInvocation.mention_id == CaseCommentMention.id,
            )
            .join(CaseComment, CaseComment.id == CaseCommentMention.comment_id)
            .where(
                CaseCommentAgentInvocation.id == invocation_id,
                CaseCommentAgentInvocation.workspace_id == self.workspace_id,
                CaseCommentMention.workspace_id == self.workspace_id,
                CaseComment.workspace_id == self.workspace_id,
            )
        )
        source = (await self.session.execute(source_statement)).tuples().one_or_none()
        if source is None:
            return None

        invoking_comment_id, case_id, parent_id = source
        thread_root_id = parent_id or invoking_comment_id
        required_ids = (thread_root_id, invoking_comment_id)
        recent_comment_ids = (
            select(CaseComment.id.label("id"))
            .where(
                CaseComment.workspace_id == self.workspace_id,
                CaseComment.case_id == case_id,
                CaseComment.parent_id == thread_root_id,
                CaseComment.id.not_in(set(required_ids)),
            )
            .order_by(CaseComment.created_at.desc(), CaseComment.surrogate_id.desc())
            .limit(_MAX_THREAD_COMMENTS - len(set(required_ids)))
            .cte("recent_thread_comment_ids")
        )
        bounded_comment_ids = (
            select(CaseComment.id.label("id"))
            .where(
                CaseComment.workspace_id == self.workspace_id,
                CaseComment.case_id == case_id,
                CaseComment.id.in_(set(required_ids)),
            )
            .union_all(select(recent_comment_ids.c.id))
            .cte("bounded_thread_comment_ids")
        )
        visible_content = sa.case(
            (
                CaseComment.deleted_at.is_not(None),
                sa.literal(_DELETED_COMMENT_CONTENT),
            ),
            else_=CaseComment.content,
        )
        bounded_content = sa.case(
            (
                sa.func.length(visible_content) > _MAX_THREAD_COMMENT_LENGTH,
                sa.func.concat(
                    sa.func.left(
                        visible_content,
                        _MAX_THREAD_COMMENT_LENGTH
                        - len(_TRUNCATED_THREAD_COMMENT_SUFFIX),
                    ),
                    _TRUNCATED_THREAD_COMMENT_SUFFIX,
                ),
            ),
            else_=visible_content,
        )
        reply_invocation = aliased(CaseCommentAgentInvocation)
        thread_statement = (
            select(
                CaseComment.id,
                CaseComment.parent_id,
                CaseComment.workflow_title,
                User,
                reply_invocation.preset_name,
                bounded_content,
                CaseComment.created_at,
            )
            .join(bounded_comment_ids, bounded_comment_ids.c.id == CaseComment.id)
            .outerjoin(User, sa.cast(CaseComment.user_id, sa.UUID) == User.id)
            .outerjoin(
                reply_invocation,
                sa.and_(
                    reply_invocation.reply_comment_id == CaseComment.id,
                    reply_invocation.workspace_id == self.workspace_id,
                ),
            )
            .order_by(CaseComment.created_at, CaseComment.surrogate_id)
        )
        rows = (await self.session.execute(thread_statement)).tuples().all()
        entries = tuple(
            CommentThreadEntry(
                id=comment_id,
                parent_id=comment_parent_id,
                author_label=_author_label(
                    workflow_title,
                    user,
                    agent_preset_name,
                ),
                content=content,
                created_at=created_at,
            )
            for (
                comment_id,
                comment_parent_id,
                workflow_title,
                user,
                agent_preset_name,
                content,
                created_at,
            ) in rows
        )
        return CommentThreadContext(
            thread_root_id=thread_root_id,
            invoking_comment_id=invoking_comment_id,
            entries=entries,
        )
