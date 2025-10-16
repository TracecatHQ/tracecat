from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic_ai.messages import (
    ToolCallPart,
)
from pydantic_ai.tools import (
    DeferredToolApprovalResult,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from sqlmodel import col, select
from temporalio import activity, workflow

from tracecat.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    ApprovalDecisionPayload,
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.approvals.models import (
    ApprovalCreate,
    ApprovalUpdate,
)
from tracecat.agent.context import AgentContext
from tracecat.common import all_activities
from tracecat.db.schemas import Approval, User
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role


@dataclass
class ApprovalWithUser:
    approval: Approval
    user: User | None = None


class ApprovalService(BaseWorkspaceService):
    """Service for managing agent approval records.

    Provides simple CRUD operations for approval records.
    Business logic (upsert, placeholder creation, etc.) should be handled
    at the orchestration layer (ApprovalManager or Temporal activities).
    """

    service_name = "approvals"

    # CREATE operations

    async def create_approval(self, params: ApprovalCreate) -> Approval:
        """Create a single approval record."""
        approval = Approval(
            owner_id=self.workspace_id,
            session_id=params.session_id,
            tool_call_id=params.tool_call_id,
            tool_name=params.tool_name,
            status=ApprovalStatus.PENDING,
            data=params.data,
        )
        self.session.add(approval)
        await self.session.commit()
        await self.session.refresh(approval)
        return approval

    async def create_approvals(
        self, approvals: Sequence[ApprovalCreate]
    ) -> list[Approval]:
        """Batch create multiple approval records."""
        if not approvals:
            return []

        records: list[Approval] = []
        for params in approvals:
            approval = Approval(
                owner_id=self.workspace_id,
                session_id=params.session_id,
                tool_call_id=params.tool_call_id,
                tool_name=params.tool_name,
                status=ApprovalStatus.PENDING,
                data=params.data,
            )
            self.session.add(approval)
            records.append(approval)

        await self.session.commit()
        for record in records:
            await self.session.refresh(record)
        return records

    # READ operations

    async def get_approval(self, approval_id: uuid.UUID) -> Approval:
        """Get a single approval by ID."""
        statement = select(Approval).where(
            Approval.owner_id == self.workspace_id,
            Approval.id == approval_id,
        )
        result = await self.session.exec(statement)
        return result.one()

    async def get_approval_by_session_and_tool(
        self,
        *,
        session_id: uuid.UUID,
        tool_call_id: str,
    ) -> Approval | None:
        """Get approval by session ID and tool call ID."""
        statement = select(Approval).where(
            Approval.owner_id == self.workspace_id,
            Approval.session_id == session_id,
            Approval.tool_call_id == tool_call_id,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def list_approvals_for_session(
        self, session_id: uuid.UUID
    ) -> Sequence[Approval]:
        """List all approvals for a given session."""
        statement = select(Approval).where(
            Approval.owner_id == self.workspace_id,
            Approval.session_id == session_id,
        )
        result = await self.session.exec(statement)
        return result.all()

    async def list_approvals_for_sessions(
        self, session_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[ApprovalWithUser]]:
        """List all approvals for a given list of session IDs."""
        statement = (
            select(Approval, User)
            .outerjoin(User, col(Approval.approved_by) == col(User.id))
            .where(
                Approval.owner_id == self.workspace_id,
                col(Approval.session_id).in_(session_ids),
            )
        )
        result = await self.session.exec(statement)
        res = defaultdict[uuid.UUID, list[ApprovalWithUser]](list)
        for approval, user in result.all():
            res[approval.session_id].append(
                ApprovalWithUser(approval=approval, user=user)
            )
        return dict(res)

    # UPDATE operations

    async def update_approval(
        self, approval: Approval, params: ApprovalUpdate
    ) -> Approval:
        """Update a single approval record."""
        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(approval, key, value)

        await self.session.commit()
        await self.session.refresh(approval)
        return approval

    async def update_approvals(
        self, updates: dict[uuid.UUID, ApprovalUpdate]
    ) -> list[Approval]:
        """Batch update multiple approval records by IDs."""
        if not updates:
            return []

        records: list[Approval] = []
        for approval_id, params in updates.items():
            approval = await self.get_approval(approval_id)
            for key, value in params.model_dump(exclude_unset=True).items():
                setattr(approval, key, value)
            records.append(approval)

        await self.session.commit()
        for record in records:
            await self.session.refresh(record)
        return records

    # DELETE operations

    async def delete_approval(self, approval: Approval) -> None:
        """Delete an approval record."""
        await self.session.delete(approval)
        await self.session.commit()


type ApprovalMap = dict[str, bool | DeferredToolApprovalResult]


class ApprovalManagerStatus(StrEnum):
    """Possible states for a deferred tool approval."""

    IDLE = "idle"
    PENDING = "pending"
    READY = "ready"


class ApprovalManager:
    def __init__(self, role: Role) -> None:
        self._approvals: ApprovalMap = {}
        self._status: ApprovalManagerStatus = ApprovalManagerStatus.IDLE
        self.role = role
        agent_ctx = AgentContext.get()
        if agent_ctx is None:
            raise RuntimeError("Agent context is not set")
        self.session_id = agent_ctx.session_id
        self._expected_tool_calls: dict[str, ToolCallPart] = {}
        self._approved_by: uuid.UUID | None = None

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        return all_activities(cls)

    @property
    def status(self) -> ApprovalManagerStatus:
        return self._status

    def is_ready(self) -> bool:
        return self._status == ApprovalManagerStatus.READY

    def set(
        self, approvals: ApprovalMap, *, approved_by: uuid.UUID | None = None
    ) -> None:
        self._approvals = approvals
        self._status = ApprovalManagerStatus.READY
        self._approved_by = approved_by

    async def wait(self) -> None:
        await workflow.wait_condition(lambda: self.is_ready())

    async def prepare(self, approvals: list[ToolCallPart]) -> None:
        self._approvals.clear()
        self._status = ApprovalManagerStatus.IDLE
        self._expected_tool_calls = {
            approval.tool_call_id: approval for approval in approvals
        }
        self._approved_by = None

        approval_payloads = [
            ToolApprovalPayload(
                tool_call_id=approval.tool_call_id,
                tool_name=approval.tool_name,
                args=approval.args,
            )
            for approval in approvals
        ]
        # Persist the approval state
        await workflow.execute_activity(
            ApprovalManager.record_approval_requests,
            arg=PersistApprovalsActivityInputs(
                role=self.role,
                session_id=self.session_id,
                approvals=approval_payloads,
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

    def get(self) -> DeferredToolResults | None:
        if not self._approvals:
            return None
        return DeferredToolResults(approvals=self._approvals)

    def validate_responses(self, approvals: ApprovalMap) -> None:
        """Validate that approval responses cover all expected tool calls."""
        if not self._expected_tool_calls:
            raise ValueError("No pending approvals to validate")
        if not approvals:
            raise ValueError("Approval responses cannot be empty")

        expected_ids = set(self._expected_tool_calls.keys())
        provided_ids = set(approvals.keys())

        missing = expected_ids - provided_ids
        if missing:
            expected_tools = [
                f"{self._expected_tool_calls[tid].tool_name} ({tid})"
                for tid in sorted(missing)
            ]
            logger.warning(
                "Missing approval responses",
                missing_count=len(missing),
                expected_tools=expected_tools,
                expected_ids=sorted(expected_ids),
                provided_ids=sorted(provided_ids),
            )
            raise ValueError(
                f"Missing approval responses for {len(missing)} tool call(s). "
                f"Expected approvals for: {', '.join(expected_tools)}"
            )

        unexpected = provided_ids - expected_ids
        if unexpected:
            logger.warning(
                "Unexpected approval responses",
                unexpected_count=len(unexpected),
                unexpected_ids=sorted(unexpected),
                expected_ids=sorted(expected_ids),
            )
            raise ValueError(
                f"Received {len(unexpected)} unexpected approval response(s) for tool call IDs: "
                f"{', '.join(sorted(unexpected))}. "
                f"Expected only: {', '.join(sorted(expected_ids))}"
            )

        for tool_call_id in expected_ids:
            if approvals[tool_call_id] is None:
                tool_name = self._expected_tool_calls[tool_call_id].tool_name
                logger.warning(
                    "Approval response is None",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )
                raise ValueError(
                    f"Approval response for tool call '{tool_name}' (ID: {tool_call_id}) "
                    f"cannot be None. Please provide a valid approval decision."
                )

    async def handle_decisions(self) -> None:
        decisions: list[ApprovalDecisionPayload] = []

        for tool_call_id, result in self._approvals.items():
            approved = False
            reason: str | None = None
            data: dict[str, Any] | None = None

            match result:
                case bool(value):
                    approved = value
                case ToolApproved(override_args=override_args):
                    approved = True
                    if override_args is not None:
                        data = {"override_args": override_args}
                case ToolDenied(message=message):
                    approved = False
                    reason = message
                    data = {"message": message}
                case _:
                    raise RuntimeError(
                        "Invalid approval result", approval_result=result
                    )
            decisions.append(
                ApprovalDecisionPayload(
                    tool_call_id=tool_call_id,
                    approved=approved,
                    reason=reason,
                    data=data,
                    approved_by=self._approved_by,
                )
            )
        if decisions:
            await workflow.execute_activity(
                ApprovalManager.apply_approval_decisions,
                arg=ApplyApprovalResultsActivityInputs(
                    role=self.role,
                    session_id=self.session_id,
                    decisions=decisions,
                ),
                start_to_close_timeout=timedelta(seconds=30),
            )
        self._approved_by = None

    @staticmethod
    @activity.defn
    async def apply_approval_decisions(
        input: ApplyApprovalResultsActivityInputs,
    ) -> None:
        """Apply approval decisions once responses are received.

        Handles business logic:
        - Creates placeholder records if they don't exist
        - Automatically sets approved_at timestamp based on status
        """
        if not input.decisions:
            return

        async with ApprovalService.with_session(role=input.role) as service:
            for decision in input.decisions:
                # Get or create approval record
                approval = await service.get_approval_by_session_and_tool(
                    session_id=input.session_id,
                    tool_call_id=decision.tool_call_id,
                )

                if approval is None:
                    # Create placeholder record if it doesn't exist
                    approval = await service.create_approval(
                        ApprovalCreate(
                            session_id=input.session_id,
                            tool_call_id=decision.tool_call_id,
                            tool_name="unknown",
                            data=None,
                        )
                    )

                # Determine status and calculate approved_at timestamp
                status = (
                    ApprovalStatus.APPROVED
                    if decision.approved
                    else ApprovalStatus.REJECTED
                )
                approved_at = (
                    datetime.now(tz=UTC) if status != ApprovalStatus.PENDING else None
                )

                # Update the approval with decision
                await service.update_approval(
                    approval,
                    ApprovalUpdate(
                        status=status,
                        reason=decision.reason,
                        data=decision.data,
                        approved_by=decision.approved_by,
                    ),
                )

                # Manually set approved_at since it's not part of ApprovalUpdate
                approval.approved_at = approved_at
                await service.session.commit()

    @staticmethod
    @activity.defn
    async def record_approval_requests(
        input: PersistApprovalsActivityInputs,
    ) -> None:
        """Persist pending approval records for deferred tool calls.

        Handles business logic:
        - Upserts approvals (updates existing or creates new)
        - Resets approval state to PENDING if record already exists
        """
        if not input.approvals:
            return

        async with ApprovalService.with_session(role=input.role) as service:
            for payload in input.approvals:
                # Check if approval already exists
                existing = await service.get_approval_by_session_and_tool(
                    session_id=input.session_id,
                    tool_call_id=payload.tool_call_id,
                )

                data = {"args": payload.args} if payload.args is not None else None

                if existing:
                    # Update existing record and reset to pending state
                    await service.update_approval(
                        existing,
                        ApprovalUpdate(
                            status=ApprovalStatus.PENDING,
                            tool_name=payload.tool_name,
                            data=data,
                            reason=None,
                            approved_by=None,
                        ),
                    )
                    # Reset approved_at manually
                    existing.approved_at = None
                    await service.session.commit()
                else:
                    # Create new approval record
                    await service.create_approval(
                        ApprovalCreate(
                            session_id=input.session_id,
                            tool_call_id=payload.tool_call_id,
                            tool_name=payload.tool_name,
                            data=data,
                        )
                    )
