from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import (
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import (
    DeferredToolApprovalResult,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from sqlmodel import col, select
from temporalio import activity, workflow
from temporalio.client import WorkflowExecution, WorkflowExecutionStatus, WorkflowHandle

from tracecat.agent.aliases import build_agent_alias
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.runtime import run_agent
from tracecat.agent.schemas import AgentOutput, ApprovalRecommendation
from tracecat.agent.service import AgentManagementService
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.contexts import ctx_role, ctx_session_id
from tracecat.db.models import Approval, User, Workflow
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import AgentActionMemo
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.identifiers import WorkflowID
from tracecat.identifiers.workflow import exec_id_to_parts
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.executions.enums import TemporalSearchAttr
from tracecat_ee.agent.activities import (
    ApplyApprovalResultsActivityInputs,
    ApprovalDecisionPayload,
    GenerateApprovalRecommendationsActivityInputs,
    PersistApprovalsActivityInputs,
    ToolApprovalPayload,
)
from tracecat_ee.agent.approvals.schemas import (
    ApprovalCreate,
    ApprovalUpdate,
)
from tracecat_ee.agent.context import AgentContext
from tracecat_ee.agent.types import AgentWorkflowID

if TYPE_CHECKING:
    from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow


@dataclass(slots=True)
class EnrichedApproval:
    approval: Approval
    approved_by: User | None = None


@dataclass(slots=True, kw_only=True)
class SessionDescription:
    start_time: datetime
    parent_workflow_id: WorkflowID | None
    root_workflow_id: WorkflowID | None
    action_ref: str | None
    action_title: str | None


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


@dataclass(slots=True, kw_only=True)
class SessionHistoryItem:
    """Represents a single execution in the session history."""

    execution: WorkflowExecution
    """The workflow execution metadata."""
    result: AgentOutput
    """The execution result, if available and successful."""


@dataclass(slots=True, kw_only=True)
class ApprovalItem:
    tool_call_id: str
    tool_name: str
    history: list[SessionHistoryItem]
    args: dict[str, Any]


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

    async def handle(
        self,
        agent_wf_id: AgentWorkflowID,
        run_id: str | None = None,
    ) -> WorkflowHandle[DurableAgentWorkflow, AgentOutput]:
        """Get a workflow handle for an agent workflow.

        Args:
            agent_wf_id: The agent workflow ID
            run_id: Optional run ID for a specific execution. If not provided,
                    gets the handle for the latest/current workflow execution.

        Returns:
            A workflow handle for the specified workflow/execution
        """
        from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

        client = await get_temporal_client()
        return client.get_workflow_handle_for(
            DurableAgentWorkflow.run, agent_wf_id, run_id=run_id
        )

    async def get_session(self, session_id: uuid.UUID) -> SessionDescription | None:
        """Get a session by ID."""

        # Query workflow executions using the agent session key
        agent_wf_id = AgentWorkflowID(session_id)

        handle = await self.handle(agent_wf_id)
        description = await handle.describe()
        memo = await description.memo()
        validated_memo = AgentActionMemo.model_validate(memo)
        parent_id, root_id = None, None
        try:
            if description.parent_id:
                parent_id, _ = exec_id_to_parts(description.parent_id)
        except Exception as e:
            logger.warning("Error parsing parent ID", error=e)
        try:
            if description.root_id:
                root_id, _ = exec_id_to_parts(description.root_id)
        except Exception as e:
            logger.warning("Error parsing root ID", error=e)
        return SessionDescription(
            start_time=description.start_time,
            parent_workflow_id=parent_id,
            root_workflow_id=root_id,
            action_ref=validated_memo.action_ref,
            action_title=validated_memo.action_title,
        )

    async def list_session_history(
        self,
        session_id: uuid.UUID,
        limit: int = 5,
    ) -> list[SessionHistoryItem]:
        """Query DurableAgentWorkflow executions sharing the same alias and fetch their results."""
        if limit <= 0:
            return []
        # get the alias for the session
        session = await self.get_session(session_id)
        if session is None:
            return []
        if session.parent_workflow_id is None or session.action_ref is None:
            logger.warning(
                "Session parent workflow ID or action ref is missing",
                session_id=session_id,
            )
            return []
        alias = build_agent_alias(session.parent_workflow_id, session.action_ref)

        query = " AND ".join(
            [
                "WorkflowType = 'DurableAgentWorkflow'",
                f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{str(self.workspace_id)}'",
                f"{TemporalSearchAttr.ALIAS.value} = '{alias}'",
            ]
        )

        page_size = max(1, min(limit, 50))
        history_items: list[SessionHistoryItem] = []
        client = await get_temporal_client()
        async for execution in client.list_workflows(
            query=query,
            page_size=page_size,
        ):
            if len(history_items) >= limit:
                break
            # Get workflow handle for specific execution run
            agent_wf_id = AgentWorkflowID.from_workflow_id(execution.id)
            handle = await self.handle(agent_wf_id, run_id=execution.run_id)

            # Try to fetch the result
            if execution.status != WorkflowExecutionStatus.COMPLETED:
                logger.warning(
                    "Workflow is not completed",
                    workflow_id=execution.id,
                    run_id=execution.run_id,
                )
                continue

            result = await handle.result()
            history_items.append(
                SessionHistoryItem(
                    execution=execution,
                    result=result,
                )
            )
        # These are the last n=limit DurableAgentWorkflow executions related to the session
        return history_items

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

    async def generate_recommendations(self) -> None:
        await workflow.execute_activity(
            ApprovalManager.generate_approval_recommendations,
            arg=GenerateApprovalRecommendationsActivityInputs(
                role=self.role,
                session_id=self.session_id,
            ),
            start_to_close_timeout=timedelta(seconds=120),
        )

    @staticmethod
    @activity.defn
    async def generate_approval_recommendations(
        input: GenerateApprovalRecommendationsActivityInputs,
    ) -> None:
        async with ApprovalService.with_session(role=input.role) as approval_service:
            approvals = await approval_service.list_approvals_for_session(
                input.session_id
            )
            history = []
            if approvals:
                try:
                    history = await approval_service.list_session_history(
                        input.session_id
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch session history for approval recommendations",
                        session_id=input.session_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

            approval_items = [
                ApprovalItem(
                    tool_call_id=approval.tool_call_id,
                    tool_name=approval.tool_name,
                    history=history,
                    args=approval.tool_call_args or {},
                )
                for approval in approvals
            ]

        async with AgentManagementService.with_session(
            role=input.role
        ) as agent_service:
            preset_id = await agent_service.get_approval_manager_preset_id()
            if not preset_id:
                logger.info(
                    "Approval manager preset not configured; skipping recommendation generation",
                    session_id=input.session_id,
                )
                return
            if agent_service.presets is None:
                logger.warning(
                    "Agent preset service unavailable for approval recommendations",
                    session_id=input.session_id,
                )
                return
            try:
                preset = await agent_service.presets.get_preset(preset_id)
            except TracecatNotFoundError:
                logger.warning(
                    "Configured approval manager preset not found",
                    preset_id=str(preset_id),
                    session_id=input.session_id,
                )
                return

            try:
                async with agent_service.with_preset_config(
                    preset_id=preset_id
                ) as preset_config:
                    for approval_item in approval_items:
                        header = (
                            "You are Tracecat's approval manager. For each pending tool call:\n"
                            "- Read the tool name, arguments, and any recent history that follows.\n"
                            "- Choose one verdict: approve (safe, low-risk), reject (dangerous, policy violation, or malformed), "
                            "or manual (uncertain, needs human review).\n"
                            "- Always provide a short, actionable reason that references the tool call (never say 'no context').\n"
                        )
                        # Extract message history from the list of SessionHistoryItem
                        message_parts = []
                        for history_item in approval_item.history:
                            if (
                                history_item.result
                                and history_item.result.message_history
                            ):
                                for msg in history_item.result.message_history:
                                    if hasattr(msg, "parts"):
                                        for part in msg.parts:
                                            # Extract user prompts
                                            if isinstance(part, UserPromptPart):
                                                message_parts.append(
                                                    f"User: {part.content}"
                                                )

                                            # Extract model text responses
                                            elif isinstance(part, TextPart):
                                                message_parts.append(
                                                    f"Assistant: {part.content}"
                                                )

                                            # Extract tool call info
                                            elif isinstance(part, ToolCallPart):
                                                message_parts.append(
                                                    f"Tool Call: {part.tool_name} with args {part.args}"
                                                )

                                            # Extract tool return results (especially errors)
                                            elif isinstance(part, ToolReturnPart):
                                                if isinstance(part.content, dict):
                                                    # For retry/error messages
                                                    if (
                                                        part.content.get("type")
                                                        == "retry"
                                                    ):
                                                        message_parts.append(
                                                            f"Tool Error ({part.tool_name}): {part.content.get('retry_message', '')}"
                                                        )
                                                    else:
                                                        message_parts.append(
                                                            f"Tool Result ({part.tool_name}): {str(part.content)[:100]}"
                                                        )

                        history_text = (
                            "\n".join(message_parts)
                            if message_parts
                            else "No recent history"
                        )
                        prompt = header
                        prompt += f"- Tool name: {approval_item.tool_name}\n"
                        prompt += f"- Tool arguments: {json.dumps(approval_item.args, indent=2)}\n"
                        prompt += f"- Tool history: {history_text}\n"
                        ctx_role.set(input.role)
                        ctx_session_id.set(input.session_id)
                        try:
                            agent_output = await run_agent(
                                user_prompt=prompt,
                                model_name=preset_config.model_name,
                                model_provider=preset_config.model_provider,
                                actions=preset_config.actions,
                                namespaces=preset_config.namespaces,
                                tool_approvals=preset_config.tool_approvals,
                                mcp_server_url=preset_config.mcp_server_url,
                                mcp_server_headers=preset_config.mcp_server_headers,
                                instructions=preset_config.instructions,
                                output_type=ApprovalRecommendation.model_json_schema(),
                                model_settings=preset_config.model_settings,
                                base_url=preset_config.base_url,
                                retries=preset_config.retries,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Approval manager agent execution failed",
                                error=str(exc),
                                session_id=input.session_id,
                                tool_call_id=approval_item.tool_call_id,
                            )
                            continue

                        # Fetch and update the approval
                        async with ApprovalService.with_session(
                            role=input.role
                        ) as approval_service:
                            approval = (
                                await approval_service.get_approval_by_session_and_tool(
                                    session_id=input.session_id,
                                    tool_call_id=approval_item.tool_call_id,
                                )
                            )
                            if approval:
                                await approval_service.update_approval(
                                    approval,
                                    ApprovalUpdate(
                                        recommendation=ApprovalRecommendation(
                                            verdict=agent_output.output["verdict"],
                                            reason=agent_output.output.get("reason"),
                                            generated_by=preset.slug,
                                        ),
                                    ),
                                )

            except (TracecatNotFoundError, TracecatAuthorizationError) as exc:
                logger.warning(
                    "Failed to load approval manager preset configuration; skipping recommendations",
                    preset_id=str(preset_id),
                    session_id=input.session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return
            except Exception as exc:
                logger.error(
                    "Unexpected error during approval recommendation generation; skipping recommendations",
                    preset_id=str(preset_id),
                    session_id=input.session_id,
                    error=str(exc),
                    exc_info=True,
                )
                return

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
