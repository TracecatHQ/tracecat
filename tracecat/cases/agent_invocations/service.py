"""Service for case-comment agent invocations."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from tracecat.cases.enums import (
    CaseCommentAgentInvocationStatus,
    MentionTargetType,
)
from tracecat.db.models import (
    AgentPreset,
    CaseCommentAgentInvocation,
    CaseCommentMention,
)
from tracecat.service import BaseWorkspaceService


class CaseCommentAgentInvocationService(BaseWorkspaceService):
    """Manage agent invocations originating from case-comment mentions."""

    service_name = "case_comment_agent_invocations"

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
        source = (
            select(
                sa.func.gen_random_uuid(),
                CaseCommentMention.id,
                AgentPreset.name,
                AgentPreset.slug,
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
