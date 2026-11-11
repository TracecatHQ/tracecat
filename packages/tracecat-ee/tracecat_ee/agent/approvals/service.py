from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio import activity, workflow

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.channel_publisher import (
    ApprovalDecided,
    ApprovalPending,
    RunUpdate,
    approval_batch_id,
    publish_budget,
    publish_updates,
)
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.mcp.metadata import strip_proxy_tool_metadata
from tracecat.agent.stream.feed import SessionFeedEventType, publish_session_event
from tracecat.agent.types import (
    DeferredToolApprovalResult,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.db.models import AgentSession, Approval, User, Workflow
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat_ee.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    ApprovalDecisionPayload,
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat_ee.agent.approvals.schemas import (
    ApprovalCreate,
    ApprovalUpdate,
)
from tracecat_ee.agent.context import AgentContext


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
            workspace_id=self.workspace_id,
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
                workspace_id=self.workspace_id,
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
            Approval.workspace_id == self.workspace_id,
            Approval.id == approval_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_approval_by_session_and_tool(
        self,
        *,
        session_id: uuid.UUID,
        tool_call_id: str,
    ) -> Approval | None:
        """Get approval by session ID and tool call ID."""
        statement = select(Approval).where(
            Approval.workspace_id == self.workspace_id,
            Approval.session_id == session_id,
            Approval.tool_call_id == tool_call_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_approvals_for_session(
        self, session_id: uuid.UUID
    ) -> Sequence[Approval]:
        """List all approvals for a given session."""
        statement = select(Approval).where(
            Approval.workspace_id == self.workspace_id,
            Approval.session_id == session_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

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
                Workflow.id.in_(list(workflow_ids))
            )
            workflow_result = await self.session.execute(workflow_statement)
            workflows = workflow_result.scalars().all()
            workflows_by_id = {w.id: w for w in workflows}

        statement = (
            select(Approval, User)
            .outerjoin(User, Approval.approved_by == User.id)
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id.in_(session_ids),
            )
        )
        result = await self.session.execute(statement)

        # Group approvals by session_id
        approvals_by_session: dict[uuid.UUID, list[tuple[Approval, User | None]]] = {}
        for approval, user in result.tuples().all():
            if approval.session_id is None:
                continue
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


type ApprovalResult = bool | DeferredToolApprovalResult
type ApprovalMap = dict[str, ApprovalResult]


class ApprovalManagerStatus(StrEnum):
    """Possible states for a deferred tool approval."""

    IDLE = "idle"
    PENDING = "pending"
    READY = "ready"


class ApprovalManager:
    def __init__(self, role: Role) -> None:
        self._approvals: ApprovalMap = {}
        self._decision_metadata_by_tool_call_id: dict[str, dict[str, Any]] = {}
        self._approved_by_by_tool_call_id: dict[str, uuid.UUID] = {}
        self._status: ApprovalManagerStatus = ApprovalManagerStatus.IDLE
        self.role = role
        agent_ctx = AgentContext.get()
        if agent_ctx is None:
            raise RuntimeError("Agent context is not set")
        self.session_id = agent_ctx.session_id
        self._expected_tool_calls: dict[str, ToolCallContent] = {}
        # Edits the pending approval message once the batch is decided.
        self._unpublished_decision: ApprovalDecided | None = None

    @classmethod
    def get_activities(cls) -> list[Callable[..., Any]]:
        return all_activities(cls)

    @property
    def status(self) -> ApprovalManagerStatus:
        return self._status

    def is_ready(self) -> bool:
        return self._status == ApprovalManagerStatus.READY

    def set(
        self,
        approvals: ApprovalMap,
        *,
        approved_by: uuid.UUID | None = None,
        decision_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Record approval decisions, accepting partial submissions.

        Parallel tool calls surface as individual approval cards that are
        decided one by one, so decisions are merged across submissions. The
        manager only becomes READY (resuming the run) once every expected
        tool call has a decision.
        """
        self._approvals.update(approvals)
        if approved_by is not None:
            for tool_call_id in approvals:
                self._approved_by_by_tool_call_id[tool_call_id] = approved_by
        if decision_metadata:
            self._decision_metadata_by_tool_call_id.update(decision_metadata)

        expected_ids = set(self._expected_tool_calls.keys())
        if expected_ids.issubset(self._approvals.keys()):
            self._status = ApprovalManagerStatus.READY
        else:
            self._status = ApprovalManagerStatus.PENDING
            logger.info(
                "Partial approval decisions recorded",
                decided=sorted(self._approvals.keys()),
                awaiting=sorted(expected_ids - set(self._approvals.keys())),
            )

    async def wait(self) -> None:
        await workflow.wait_condition(lambda: self.is_ready())

    async def prepare(
        self,
        approvals: list[ToolCallContent],
        *,
        request_metadata: dict[str, dict[str, Any]] | None = None,
        publish_to: uuid.UUID | None = None,
    ) -> None:
        """Persist the requests, then post them to the run's linked channel, if any."""
        self._approvals.clear()
        self._decision_metadata_by_tool_call_id.clear()
        self._status = ApprovalManagerStatus.IDLE
        self._expected_tool_calls = {approval.id: approval for approval in approvals}
        self._approved_by_by_tool_call_id.clear()

        # Preserve per-tool proxy metadata on approval rows for display and
        # decision reconciliation.
        approval_payloads = [
            ToolApprovalPayload(
                tool_call_id=approval.id,
                tool_name=approval.name,
                args=approval.input,
                metadata=(request_metadata or {}).get(approval.id),
            )
            for approval in approvals
        ]
        pending = (
            ApprovalPending(
                session_id=self.session_id,
                run_id=publish_to,
                batch_id=approval_batch_id(publish_to, [item.id for item in approvals]),
            )
            if publish_to is not None
            else None
        )
        updates: list[RunUpdate] = [pending] if pending is not None else []
        # Persist the approval state (and chat messages atomically for chat namespace)
        pending_ref = await workflow.execute_activity(
            ApprovalManager.record_approval_requests,
            arg=PersistApprovalsActivityInputs(
                role=self.role,
                session_id=self.session_id,
                approvals=approval_payloads,
                channel_updates=updates,
            ),
            start_to_close_timeout=timedelta(seconds=30) + publish_budget(updates),
        )
        self._unpublished_decision = (
            ApprovalDecided(
                session_id=self.session_id,
                batch_id=pending.batch_id,
                pending_ref=pending_ref,
            )
            if pending is not None
            else None
        )

    def take_unpublished_decision(self) -> list[RunUpdate]:
        """The update closing the open approval message, once; empty when none is open."""
        decided, self._unpublished_decision = self._unpublished_decision, None
        return [decided] if decided is not None else []

    def get(self) -> DeferredToolResults | None:
        if not self._approvals:
            return None
        return DeferredToolResults(approvals=self._approvals)

    def get_decision(
        self, tool_call_id: str
    ) -> bool | DeferredToolApprovalResult | None:
        """Get the approval decision for a specific tool call ID."""
        return self._approvals.get(tool_call_id)

    def validate_responses(self, approvals: ApprovalMap) -> None:
        """Validate approval responses against the expected tool calls.

        Partial submissions are valid: parallel tool calls are decided one
        card at a time, and ``set`` accumulates decisions until every expected
        tool call is covered. Responses for unknown tool calls or with missing
        decisions are rejected.
        """
        if not self._expected_tool_calls:
            raise ValueError("No pending approvals to validate")
        if not approvals:
            raise ValueError("Approval responses cannot be empty")

        expected_ids = set(self._expected_tool_calls.keys())
        provided_ids = set(approvals.keys())

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

        for tool_call_id in provided_ids:
            if approvals[tool_call_id] is None:
                tool_name = self._expected_tool_calls[tool_call_id].name
                logger.warning(
                    "Approval response is None",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )
                raise ValueError(
                    f"Approval response for tool call '{tool_name}' (ID: {tool_call_id}) "
                    f"cannot be None. Please provide a valid approval decision."
                )

    async def handle_decisions(self, *, publish: bool = True) -> None:
        """Record the decisions; ``publish=False`` keeps the message open for later."""
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
                    decision_metadata=self._decision_metadata_by_tool_call_id.get(
                        tool_call_id
                    ),
                    approved_by=self._approved_by_by_tool_call_id.get(tool_call_id),
                )
            )
        if decisions:
            decided = self._unpublished_decision
            updates: list[RunUpdate] = [decided] if publish and decided else []
            await workflow.execute_activity(
                ApprovalManager.apply_approval_decisions,
                arg=ApplyApprovalResultsActivityInputs(
                    role=self.role,
                    session_id=self.session_id,
                    decisions=decisions,
                    channel_updates=updates,
                ),
                start_to_close_timeout=timedelta(seconds=30) + publish_budget(updates),
            )
            # Cleared only once published, so a failed apply still closes it at finalize.
            if updates:
                self._unpublished_decision = None
        self._approved_by_by_tool_call_id.clear()
        self._decision_metadata_by_tool_call_id.clear()

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
            now = datetime.now(tz=UTC)
            # Untyped: these are SQLAlchemy insert values keyed by column name,
            # where each value may be a Python scalar or a SQL element (null()).
            rows: list[dict[str, Any]] = []
            for decision in input.decisions:
                status = (
                    ApprovalStatus.APPROVED
                    if decision.approved
                    else ApprovalStatus.REJECTED
                )
                resolved_decision = decision.decision
                if decision.decision_metadata:
                    if isinstance(resolved_decision, dict):
                        resolved_decision = {
                            **resolved_decision,
                            "metadata": decision.decision_metadata,
                        }
                    elif isinstance(resolved_decision, bool):
                        resolved_decision = {
                            "value": resolved_decision,
                            "metadata": decision.decision_metadata,
                        }
                    elif resolved_decision is None:
                        resolved_decision = {"metadata": decision.decision_metadata}
                rows.append(
                    {
                        "workspace_id": service.workspace_id,
                        "session_id": input.session_id,
                        "tool_call_id": decision.tool_call_id,
                        "tool_name": "unknown",
                        "tool_call_args": None,
                        "status": status,
                        "reason": decision.reason,
                        "approved_by": decision.approved_by,
                        "approved_at": now,
                        # SQL NULL (not Python None) so COALESCE preserves a stored
                        # decision instead of overwriting it with JSON null.
                        "decision": resolved_decision
                        if resolved_decision is not None
                        else null(),
                    }
                )

            stmt = pg_insert(Approval).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_approval_workspace_session_tool",
                set_={
                    "status": stmt.excluded.status,
                    "reason": stmt.excluded.reason,
                    "approved_by": stmt.excluded.approved_by,
                    "approved_at": stmt.excluded.approved_at,
                    "decision": func.coalesce(
                        stmt.excluded.decision, Approval.decision
                    ),
                    # onupdate does not fire for ON CONFLICT DO UPDATE.
                    "updated_at": func.now(),
                },
            )
            await service.session.execute(stmt)
            await service.session.commit()
        await publish_updates(input.role, input.channel_updates)

    @staticmethod
    @activity.defn
    async def record_approval_requests(
        input: PersistApprovalsActivityInputs,
    ) -> str | None:
        """Persist pending approval records for deferred tool calls.

        Returns the posted approval message's ref; None when nothing was
        published, which workflow code from before publishing also decodes.

        Handles business logic:
        - Upserts approvals (updates existing or creates new)
        - Resets approval state to PENDING if record already exists
        """

        if not input.approvals:
            return None

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
                        approval_args = strip_proxy_tool_metadata(payload.args)
                    elif isinstance(payload.args, str):
                        try:
                            loaded_args = json.loads(payload.args)
                            if isinstance(loaded_args, dict):
                                approval_args = strip_proxy_tool_metadata(loaded_args)
                            else:
                                approval_args = {"raw_args": payload.args}
                        except (json.JSONDecodeError, ValueError):
                            # Store as-is if not valid JSON
                            approval_args = {"raw_args": payload.args}
                if payload.metadata:
                    approval_args = approval_args or {}
                    approval_args["_tracecat"] = payload.metadata

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
            owner = (
                await service.session.execute(
                    select(
                        AgentSession.curr_run_id, AgentSession.active_stream_id
                    ).where(
                        AgentSession.id == input.session_id,
                        AgentSession.workspace_id == service.workspace_id,
                    )
                )
            ).one_or_none()

        if owner is not None and owner.curr_run_id is not None:
            await publish_session_event(
                workspace_id=service.workspace_id,
                session_id=input.session_id,
                type=SessionFeedEventType.RUN_PAUSED,
                run_id=owner.curr_run_id,
                stream_id=owner.active_stream_id,
            )
        refs = await publish_updates(input.role, input.channel_updates)
        # At most one update: the batch's ApprovalPending.
        return refs[0] if refs else None
