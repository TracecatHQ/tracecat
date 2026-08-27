"""Persistence service for case-to-agent-session interactions."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.cases.enums import CaseAgentSessionInteractionOperation
from tracecat.contexts import ctx_agent_session_id
from tracecat.db.models import AgentSession, Case, CaseAgentSessionInteraction
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService


class CaseAgentSessionInteractionService(BaseWorkspaceService):
    """Record the cases touched by Inbox-facing agent sessions."""

    service_name = "case_agent_session_interactions"

    async def record_from_context(
        self,
        *,
        case_id: uuid.UUID,
        operation: CaseAgentSessionInteractionOperation,
    ) -> CaseAgentSessionInteraction | None:
        """Record a trusted request's case interaction when provenance is present.

        Args:
            case_id: Case touched by the current request.
            operation: Type of case mutation performed.

        Returns:
            The inserted or refreshed interaction, or ``None`` when the request
            has no verified agent-session provenance.
        """
        if (agent_session_id := ctx_agent_session_id.get()) is None:
            return None
        return await self.record(
            case_id=case_id,
            agent_session_id=agent_session_id,
            operation=operation,
        )

    async def resolve_root_session_id(self, session_id: uuid.UUID) -> uuid.UUID:
        """Resolve a session or continuation to its Inbox-facing root session.

        Args:
            session_id: Agent session to resolve.

        Returns:
            The root agent session ID.

        Raises:
            TracecatNotFoundError: If the session lineage leaves the workspace.
            TracecatValidationError: If the session lineage contains a cycle.
        """
        current_session_id = session_id
        visited: set[uuid.UUID] = set()

        while current_session_id not in visited:
            visited.add(current_session_id)
            statement = select(
                AgentSession.id,
                AgentSession.parent_session_id,
            ).where(
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.id == current_session_id,
            )
            result = (await self.session.execute(statement)).one_or_none()
            if result is None:
                raise TracecatNotFoundError(
                    f"Agent session '{session_id}' not found in this workspace"
                )

            resolved_session_id, parent_session_id = result
            if parent_session_id is None:
                return resolved_session_id
            current_session_id = parent_session_id

        raise TracecatValidationError("Agent session lineage contains a cycle")

    async def record(
        self,
        *,
        case_id: uuid.UUID,
        agent_session_id: uuid.UUID,
        operation: CaseAgentSessionInteractionOperation,
    ) -> CaseAgentSessionInteraction:
        """Upsert one case interaction without committing the caller's transaction.

        Args:
            case_id: Case touched by the agent session.
            agent_session_id: Session that performed the operation.
            operation: Type of case operation performed.

        Returns:
            The inserted or refreshed interaction.

        Raises:
            TracecatNotFoundError: If the case or session is outside the workspace.
        """
        case_exists = await self.session.scalar(
            select(Case.id).where(
                Case.workspace_id == self.workspace_id,
                Case.id == case_id,
            )
        )
        if case_exists is None:
            raise TracecatNotFoundError(f"Case '{case_id}' not found in this workspace")

        root_session_id = await self.resolve_root_session_id(agent_session_id)
        statement = pg_insert(CaseAgentSessionInteraction).values(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            case_id=case_id,
            agent_session_id=root_session_id,
            operation=operation,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                "workspace_id",
                "case_id",
                "agent_session_id",
                "operation",
            ],
            set_={"updated_at": func.now()},
        )
        result = await self.session.scalars(
            statement.returning(CaseAgentSessionInteraction),
            execution_options={"populate_existing": True},
        )
        return result.one()
