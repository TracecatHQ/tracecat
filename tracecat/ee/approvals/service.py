"""Service for managing approvals in GraphAgent workflows."""

import uuid
from collections.abc import Sequence

from sqlmodel import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.db.schemas import Approval
from tracecat.ee.approvals.models import (
    ApprovalCreate,
    ApprovalUpdate,
    CreateApprovalActivityInputs,
    UpdateApprovalActivityInputs,
)
from tracecat.service import BaseWorkspaceService


class ApprovalsService(BaseWorkspaceService):
    """Service for GraphAgent approval records."""

    service_name = "approvals"

    async def create_approval(self, params: ApprovalCreate) -> Approval:
        """Create a new approval record.

        Args:
            params: Approval creation parameters

        Returns:
            The created approval record
        """
        approval = Approval(
            session_id=params.session_id,
            type=params.type,
            status=params.status,
            data=params.data,
            actor=params.actor,
            owner_id=self.workspace_id,
        )
        self.session.add(approval)
        await self.session.commit()
        await self.session.refresh(approval)
        return approval

    async def get_approval(self, approval_id: uuid.UUID) -> Approval | None:
        """Get an approval by ID.

        Args:
            approval_id: The approval ID

        Returns:
            The approval record if found, None otherwise
        """
        stmt = select(Approval).where(
            Approval.owner_id == self.workspace_id, Approval.id == approval_id
        )
        res = await self.session.exec(stmt)
        return res.first()

    async def update_approval(
        self, approval: Approval, params: ApprovalUpdate
    ) -> Approval:
        """Update an existing approval.

        Args:
            approval: The approval to update
            params: Update parameters

        Returns:
            The updated approval record
        """
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(approval, key, value)
        self.session.add(approval)
        await self.session.commit()
        await self.session.refresh(approval)
        return approval

    async def list_approvals(
        self, *, session_id: str | None = None
    ) -> Sequence[Approval]:
        """List approvals, optionally filtered by session ID.

        Args:
            session_id: Optional session ID to filter by

        Returns:
            List of approval records
        """
        stmt = select(Approval).where(Approval.owner_id == self.workspace_id)
        if session_id:
            stmt = stmt.where(Approval.session_id == session_id)
        res = await self.session.exec(stmt)
        return res.all()

    async def delete_approval(self, approval: Approval) -> None:
        """Delete an approval record.

        Args:
            approval: The approval to delete
        """
        await self.session.delete(approval)
        await self.session.commit()

    # ── Temporal activities ──────────────────────────────────────────
    @staticmethod
    @activity.defn
    async def create_approval_activity(
        input: CreateApprovalActivityInputs,
    ) -> uuid.UUID:
        """Temporal activity for creating an approval.

        Args:
            input: Activity input containing role and creation parameters

        Returns:
            The ID of the created approval
        """
        async with ApprovalsService.with_session(role=input.role) as svc:
            approval = await svc.create_approval(input.params)
            svc.logger.info("Approval created (activity)", approval_id=approval.id)
            return approval.id

    @staticmethod
    @activity.defn
    async def update_approval_activity(
        input: UpdateApprovalActivityInputs,
    ) -> uuid.UUID:
        """Temporal activity for updating an approval.

        Args:
            input: Activity input containing role, approval ID, and update parameters

        Returns:
            The ID of the updated approval

        Raises:
            ApplicationError: If the approval is not found
        """
        async with ApprovalsService.with_session(role=input.role) as svc:
            approval = await svc.get_approval(input.approval_id)
            if approval is None:
                raise ApplicationError("Approval not found", non_retryable=True)
            await svc.update_approval(approval, input.params)
            return approval.id
