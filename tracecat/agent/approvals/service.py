from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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
from tracecat.db.schemas import Approval, User, Workflow
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role


@dataclass(slots=True)
class EnrichedApproval:
    approval: Approval
    approved_by: User | None = None


@dataclass(slots=True, kw_only=True)
class EnrichedSession:
    id: uuid.UUID
    approvals: list[EnrichedApproval] = field(default_factory=list)
    parent_workflow: Workflow | None = None
    root_workflow: Workflow | None = None
    start_time: datetime
    action_ref: str | None = None
    action_title: str | None = None


@dataclass(slots=True, kw_only=True)
class SessionInfo:
    session_id: uuid.UUID
    start_time: datetime
    parent_workflow_id: uuid.UUID | None = None
    root_workflow_id: uuid.UUID | None = None
    action_ref: str | None = None
    action_title: str | None = None


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
            tool_call_args=params.tool_call_args,
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
                tool_call_args=params.tool_call_args,
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

    async def list_sessions_enriched(
        self, sessions: Sequence[SessionInfo]
    ) -> list[EnrichedSession]:
        """List all enriched sessions for a given list of session IDs."""
        session_ids = [s.session_id for s in sessions]

        # Get unique workflow IDs to fetch
        workflow_ids = set()
        for session in sessions:
            if session.parent_workflow_id:
                workflow_ids.add(session.parent_workflow_id)
            if session.root_workflow_id:
                workflow_ids.add(session.root_workflow_id)

        # Fetch workflows if any exist
        workflows_by_id = {}
        if workflow_ids:
            workflow_statement = select(Workflow).where(
                col(Workflow.id).in_(list(workflow_ids))
            )
            workflow_result = await self.session.exec(workflow_statement)
            workflows_by_id = {w.id: w for w in workflow_result.all()}

        statement = (
            select(Approval, User)
            .outerjoin(User, col(Approval.approved_by) == col(User.id))
            .where(
                Approval.owner_id == self.workspace_id,
                col(Approval.session_id).in_(session_ids),
            )
        )
        result = await self.session.exec(statement)

        # Group approvals by session_id
        approvals_by_session: dict[uuid.UUID, list[tuple[Approval, User | None]]] = {}
        for approval, user in result.all():
            if approval.session_id not in approvals_by_session:
                approvals_by_session[approval.session_id] = []
            approvals_by_session[approval.session_id].append((approval, user))

        # Create one EnrichedSession per session with all its approvals
        res: list[EnrichedSession] = []
        for session in sessions:
            id = session.session_id
            parent_wf_id = session.parent_workflow_id
            root_wf_id = session.root_workflow_id
            session_approvals = approvals_by_session.get(id, [])

            # Create enriched approval objects
            enriched_approvals = [
                EnrichedApproval(approval=approval, approved_by=user)
                for approval, user in session_approvals
            ]

            # Create the enriched session
            enriched_session = EnrichedSession(
                id=id,
                approvals=enriched_approvals,
                parent_workflow=workflows_by_id.get(parent_wf_id)
                if parent_wf_id
                else None,
                root_workflow=workflows_by_id.get(root_wf_id) if root_wf_id else None,
                start_time=session.start_time,
                action_ref=session.action_ref,
                action_title=session.action_title,
            )
            res.append(enriched_session)

        return res

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
            decision: bool | dict[str, Any] | None = None

            match result:
                case bool(value):
                    approved = value
                    decision = value
                case ToolApproved(override_args=override_args):
                    approved = True
                    decision_payload: dict[str, Any] = {"kind": "tool-approved"}
                    if override_args is not None:
                        decision_payload["override_args"] = override_args
                    decision = decision_payload
                case ToolDenied(message=message):
                    approved = False
                    reason = message
                    decision_payload = {"kind": "tool-denied"}
                    if message:
                        decision_payload["message"] = message
                    decision = decision_payload
                case _:
                    raise RuntimeError(
                        "Invalid approval result", approval_result=result
                    )
            decisions.append(
                ApprovalDecisionPayload(
                    tool_call_id=tool_call_id,
                    approved=approved,
                    reason=reason,
                    decision=decision,
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
                            tool_call_args=None,
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

                # Build update payload - only include decision if it was explicitly set
                update_data = {
                    "status": status,
                    "reason": decision.reason,
                    "approved_by": decision.approved_by,
                }
                if decision.decision is not None:
                    update_data["decision"] = decision.decision

                # Update the approval with decision
                await service.update_approval(
                    approval,
                    ApprovalUpdate(**update_data),
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

                # Normalize tool args to a dict
                approval_args: dict[str, Any] | None = None
                if payload.args is not None:
                    if isinstance(payload.args, dict):
                        approval_args = payload.args
                    elif isinstance(payload.args, str):
                        try:
                            approval_args = json.loads(payload.args)
                        except (json.JSONDecodeError, ValueError):
                            # Store as-is if not valid JSON
                            approval_args = {"raw_args": payload.args}

                if existing:
                    # TODO: Do we need this path?
                    # Update existing record and reset to pending state
                    await service.update_approval(
                        existing,
                        ApprovalUpdate(
                            status=ApprovalStatus.PENDING,
                            tool_name=payload.tool_name,
                            tool_call_args=approval_args,
                            reason=None,
                            approved_by=None,
                            decision=None,
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
                            tool_call_args=approval_args,
                        )
                    )
