"""Service for managing agent sessions."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast

import orjson
from pydantic_ai.messages import (
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai.tools import ToolApproved, ToolDenied
from sqlalchemy import (
    TIMESTAMP,
    String,
    and_,
    case,
    column,
    delete,
    func,
    literal,
    or_,
    select,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import SQLAlchemyError
from temporalio.client import (
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import Priority, TypedSearchAttributes
from temporalio.service import RPCError
from tracecat_ee.workspace_chat.policy import is_workspace_chat_entitled
from tracecat_ee.workspace_chat.skills import (
    BUILTIN_WORKSPACE_CHAT_SKILLS,
)
from tracecat_registry._internal.exceptions import SecretNotFoundError

import tracecat.agent.adapter.vercel
import tracecat.artifacts.projection as artifact_projection
from tracecat import config
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.approvals.types import (
    BooleanApprovalDecision,
    PersistedApprovalDecision,
    ToolApprovedDecision,
    ToolDeniedDecision,
)
from tracecat.agent.cancellation import signal_turn_cancel
from tracecat.agent.common.stream_types import (
    ApprovalStreamStatus,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.llm import LLMCompletionError
from tracecat.agent.mcp.metadata import sanitize_message_tool_inputs
from tracecat.agent.preset.prompts import AgentPresetBuilderPrompt
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.runtime.claude_code.session_lines import (
    APPROVAL_INTERRUPT_CONTENT_EXACT,
    APPROVAL_INTERRUPT_CONTENT_MARKERS,
    is_approval_interrupt_tool_result,
    is_continuation_control_artifact,
    session_line_uuid,
)
from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.service import AgentManagementService
from tracecat.agent.session.schemas import (
    AgentSessionCancelResponse,
    AgentSessionCreate,
    AgentSessionRead,
    AgentSessionUpdate,
)
from tracecat.agent.session.title_generator import generate_session_title
from tracecat.agent.session.types import (
    AgentSessionEntity,
    TurnLifecycle,
    TurnLifecycleResult,
)
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.subagents import (
    ResolvedAgentsConfig,
)
from tracecat.agent.types import AgentConfig, ClaudeSDKMessageTA
from tracecat.artifacts.bindings import ArtifactSideEffect
from tracecat.artifacts.schemas import Artifact, ArtifactAdapter, ArtifactType
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.prompts import CaseCopilotPrompts
from tracecat.cases.service import CasesService
from tracecat.chat.enums import MessageKind
from tracecat.chat.schemas import (
    ApprovalRead,
    BasicChatRequest,
    ChatMessage,
    ChatReadMinimal,
    ChatRequest,
    ChatResponse,
    ContinueRunRequest,
    VercelChatRequest,
)
from tracecat.chat.service import ChatService
from tracecat.chat.tools import (
    filter_workspace_chat_tools_for_entitlements,
    filter_workspace_chat_tools_for_scopes,
    get_default_tools,
)
from tracecat.db.models import (
    APPROVAL_STATUS_ENUM,
    AgentSession,
    AgentSessionHistory,
    Approval,
    Case,
    Chat,
    User,
    Workflow,
)
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import UserID
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat.workflow.executions.correlation import build_agent_session_correlation_id
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)
from tracecat.workspaces.prompts import WorkspaceCopilotPrompts

if TYPE_CHECKING:
    from tracecat_ee.agent.approvals.service import ApprovalMap, ApprovalResult

    from tracecat.agent.executor.activity import ToolExecutionResult

AUTO_TITLE_SERVICE_ID = "tracecat-api"
APPROVAL_CONTINUATION_DEDUP_TTL_SECONDS = 5 * 60
_background_tasks: set[asyncio.Task[None]] = set()


async def _auto_title_session(
    *,
    session_id: uuid.UUID,
    user_prompt: str,
    expected_title: str,
    role: Role,
) -> None:
    """Best-effort auto-title using a fresh database session.

    Expected generation errors are handled inside
    ``auto_title_session_on_first_prompt``; anything else propagates to the
    task's done callback so programming/database faults surface in logs.
    """
    async with AgentSessionService.with_session(role=role) as service:
        agent_session = await service.get_session(session_id)
        if agent_session is None:
            logger.info(
                "session_auto_title_skip",
                session_id=str(session_id),
                prompt_length=len(user_prompt.strip()),
                reason="session_not_found",
            )
            return
        await service.auto_title_session_on_first_prompt(
            agent_session,
            user_prompt,
            expected_title=expected_title,
        )


def _finalize_auto_title_task(
    task: asyncio.Task[None], *, session_id: uuid.UUID
) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.error(
            "session_auto_title_failure",
            session_id=str(session_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )


@dataclass
class SessionHistoryData:
    """Data structure for session history loaded from DB."""

    sdk_session_id: str
    sdk_session_data: str
    is_fork: bool = False  # If True, SDK should use fork_session=True


class _ApprovalDecisionFields(NamedTuple):
    """Approval row fields derived from a deferred approval result."""

    status: ApprovalStatus
    reason: str | None
    decision: PersistedApprovalDecision
    approved_by: uuid.UUID | None
    approved_at: datetime


def _approval_decision_fields(
    result: ApprovalResult,
    *,
    approved_by: uuid.UUID | None,
    decision_metadata: ApprovalDecisionMetadata | None,
) -> _ApprovalDecisionFields:
    """Map a deferred approval result into Approval row update fields."""
    status: ApprovalStatus
    reason: str | None = None
    decision: PersistedApprovalDecision

    match result:
        case bool(value):
            status = ApprovalStatus.APPROVED if value else ApprovalStatus.REJECTED
            if decision_metadata:
                boolean_decision: BooleanApprovalDecision = {
                    "value": value,
                    "metadata": decision_metadata,
                }
                decision = boolean_decision
            else:
                decision = value
        case ToolApproved(override_args=override_args):
            status = ApprovalStatus.APPROVED
            approved_decision: ToolApprovedDecision = {"kind": "tool-approved"}
            if override_args is not None:
                approved_decision["override_args"] = override_args
            if decision_metadata:
                approved_decision["metadata"] = decision_metadata
            decision = approved_decision
        case ToolDenied(message=message):
            status = ApprovalStatus.REJECTED
            reason = message
            denied_decision: ToolDeniedDecision = {"kind": "tool-denied"}
            if message:
                denied_decision["message"] = message
            if decision_metadata:
                denied_decision["metadata"] = decision_metadata
            decision = denied_decision
        case _:
            raise ValueError(f"Unsupported approval result: {type(result)}")

    return _ApprovalDecisionFields(
        status=status,
        reason=reason,
        decision=decision,
        approved_by=approved_by,
        approved_at=datetime.now(tz=UTC),
    )


def _decision_matches_persisted(
    result: ApprovalResult, persisted: PersistedApprovalDecision | None
) -> bool:
    """Return whether a resubmitted decision matches what was already stored.

    Compares outcome and override args but not metadata, so the same decision
    replayed from a different surface (Slack <-> inbox) is still a match.
    """
    if persisted is None:
        return False

    fields = _approval_decision_fields(result, approved_by=None, decision_metadata=None)
    submitted = fields.decision
    # A bare bool is the legacy shape; {"value": ...} is the same decision
    # enriched with submission metadata, so the two must compare equal.
    if isinstance(submitted, bool):
        if isinstance(persisted, bool):
            return submitted == persisted
        return submitted == persisted.get("value")
    if isinstance(persisted, bool):
        return False
    # Compare the outcome only. Metadata and the deny message are excluded:
    # each surface supplies its own default reason, so including them would
    # reject a replay of the same decision from a different surface.
    return all(
        submitted.get(key) == persisted.get(key) for key in ("kind", "override_args")
    )


@dataclass(frozen=True)
class StreamResumeState:
    """Transport-neutral state for deciding whether a session stream is live."""

    lifecycle: TurnLifecycle
    curr_run_id: uuid.UUID | None
    active_stream_id: uuid.UUID | None
    has_live_stream: bool


@dataclass(frozen=True)
class ApprovalContinuationAttempt:
    """A persisted, retryable approval-continuation stream attempt."""

    stream: AgentStream
    stream_id: uuid.UUID
    previous_stream_id: uuid.UUID | None


type ApprovalDecisionMetadata = dict[str, Any]
"""Submission metadata persisted alongside a decision.

Always carries ``source``; client-supplied keys are merged over it, so the
value type stays open. ``_decision_matches_persisted`` ignores this entirely
so the same decision replayed from another surface is not a conflict.
"""


@dataclass(frozen=True)
class _ValidatedContinuation:
    """Approvals resolved from a validated continuation request."""

    approval_map: ApprovalMap
    decision_metadata: dict[str, ApprovalDecisionMetadata]


class _DecisionRow(NamedTuple):
    """One row of the VALUES clause; field order must match the columns."""

    tool_call_id: str
    status: ApprovalStatus
    reason: str | None
    decision: PersistedApprovalDecision
    approved_at: datetime


class AgentSessionService(BaseWorkspaceService):
    """Service for managing agent sessions and history."""

    service_name = "agent-session"

    async def _get_default_tools(self, entity_type: AgentSessionEntity) -> list[str]:
        """Get entitlement-aware default tools for a session entity type."""
        agent_addons_enabled = True
        if entity_type is AgentSessionEntity.WORKSPACE_CHAT:
            agent_addons_enabled = await self.has_entitlement(Entitlement.AGENT_ADDONS)
        return get_default_tools(
            entity_type.value,
            agent_addons_enabled=agent_addons_enabled,
        )

    async def _workspace_chat_tools_for_entitlements(
        self,
        tools: list[str] | None,
    ) -> list[str] | None:
        """Filter stored Workspace chat tools by current entitlements."""
        if tools is None:
            return None
        return filter_workspace_chat_tools_for_entitlements(
            tools,
            agent_addons_enabled=await self.has_entitlement(Entitlement.AGENT_ADDONS),
        )

    async def _resolve_builtin_workspace_chat_skills(self) -> list[str] | None:
        """Always-on platform skills staged for entitled workspace-chat sessions.

        Returns the reserved-prefix skill names to stage into the copilot's
        skills directory, or ``None`` when the org is not entitled to Workspace
        Chat or the Enterprise package is unavailable. Names only — the executor
        resolves each to a packaged skill directory at stage time.
        """

        if not await is_workspace_chat_entitled(self.session, self.role):
            return None
        return list(BUILTIN_WORKSPACE_CHAT_SKILLS)

    async def _resolve_workspace_chat_actions(
        self,
        agent_session: AgentSession,
    ) -> list[str] | None:
        """Merge always-on Workspace chat defaults with the session's extras.

        Defaults are derived at runtime (never frozen per session), so they stay
        current and are always present. ``agent_session.tools`` holds only the
        extra tools the user added in the chat tools dialog.
        """
        defaults = await self._get_default_tools(AgentSessionEntity.WORKSPACE_CHAT)
        extras = agent_session.tools or []
        merged = list(dict.fromkeys([*defaults, *extras]))
        # Chat tools execute under the executor service principal, which the
        # internal API routes authorize against the service allowlist rather than
        # the chat user's RBAC. Enforce the caller's action scopes here -- the
        # last point where the user's real role is available -- so `agent:execute`
        # alone cannot grant workflow create/edit or case delete via these tools.
        merged = filter_workspace_chat_tools_for_scopes(merged, role=self.role)
        return await self._workspace_chat_tools_for_entitlements(merged)

    async def _resolve_session_mcp_servers(
        self,
        agent_session: AgentSession,
        agent_svc: AgentManagementService,
    ) -> list[MCPServerConfig] | None:
        """Resolve attached MCP integration IDs into boundary-safe server refs."""
        if (
            not agent_session.mcp_integrations
            or agent_svc.presets is None
            or not await self.has_entitlement(Entitlement.AGENT_ADDONS)
        ):
            return None
        return await agent_svc.presets.resolve_mcp_integration_refs(
            agent_session.mcp_integrations
        )

    async def _validate_session_mcp_integrations(
        self, mcp_integrations: list[str] | None
    ) -> None:
        """Validate session-attached MCP integrations before persistence."""
        if not mcp_integrations:
            return
        preset_service = AgentPresetService(self.session, self.role)
        await preset_service.load_selected_mcp_integrations(mcp_integrations)

    def _build_direct_agent_search_attributes(
        self, session_id: uuid.UUID
    ) -> TypedSearchAttributes:
        """Build Temporal search attributes for direct (non-child) agent runs."""
        pairs = [
            TriggerType.MANUAL.to_temporal_search_attr_pair(),
            ExecutionType.PUBLISHED.to_temporal_search_attr_pair(),
            TemporalSearchAttr.CORRELATION_ID.create_pair(
                build_agent_session_correlation_id(session_id)
            ),
        ]
        if self.role.user_id is not None:
            pairs.append(
                TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair(
                    str(self.role.user_id)
                )
            )
        if self.role.workspace_id is not None:
            pairs.append(
                TemporalSearchAttr.WORKSPACE_ID.create_pair(str(self.role.workspace_id))
            )
        return TypedSearchAttributes(search_attributes=pairs)

    async def create_session(
        self,
        args: AgentSessionCreate,
        *,
        channel_context: dict[str, Any] | None = None,
        agents_binding: ResolvedAgentsConfig | None = None,
    ) -> AgentSession:
        """Create a new agent session.

        Args:
            args: Session creation parameters.
            channel_context: Trusted external channel metadata to bind to session.
            agents_binding: Already-resolved internal subagent binding from the
                workflow.

        Returns:
            The created AgentSession model.
        """
        # Apply default tools based on entity type if tools not provided.
        # Workspace chat merges its always-on defaults at runtime instead, so
        # ``tools`` stores only the extras the user added (never the defaults).
        tools = args.tools
        if (
            not tools
            and args.entity_type
            and args.entity_type is not AgentSessionEntity.WORKSPACE_CHAT
        ):
            tools = await self._get_default_tools(args.entity_type)
        logical_preset_id = self._resolve_logical_preset_id(
            entity_type=args.entity_type,
            entity_id=args.entity_id,
            agent_preset_id=args.agent_preset_id,
        )
        pinned_preset_version_id = await self._validate_preset_version_for_assignment(
            entity_type=args.entity_type,
            entity_id=args.entity_id,
            agent_preset_id=args.agent_preset_id,
            agent_preset_version_id=args.agent_preset_version_id,
        )
        if agents_binding is not None:
            resolved_agents_binding = agents_binding.model_dump(mode="json")
        else:
            resolved_agents_binding = (
                await self._resolve_agents_binding_for_preset_version_id(
                    pinned_preset_version_id
                )
            )
        await self._validate_session_mcp_integrations(args.mcp_integrations)

        agent_session = AgentSession(
            workspace_id=self.workspace_id,
            # Metadata
            title=args.title,
            created_by=self.role.user_id,
            entity_type=args.entity_type.value,
            entity_id=args.entity_id,
            channel_context=channel_context,
            tools=tools,
            mcp_integrations=args.mcp_integrations,
            agent_preset_id=logical_preset_id,
            agent_preset_version_id=pinned_preset_version_id,
            agents_binding=resolved_agents_binding,
            # Harness
            harness_type=args.harness_type,
        )
        # Use provided ID if given, otherwise DB default generates one
        if args.id:
            agent_session.id = args.id
        self.session.add(agent_session)
        await self.session.commit()
        await self.session.refresh(agent_session)
        return agent_session

    def _resolve_logical_preset_id(
        self,
        *,
        entity_type: AgentSessionEntity,
        entity_id: uuid.UUID,
        agent_preset_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        """Resolve the logical preset ID for a session assignment."""
        if entity_type is AgentSessionEntity.AGENT_PRESET:
            return entity_id
        if agent_preset_id is not None:
            return agent_preset_id
        if entity_type is AgentSessionEntity.EXTERNAL_CHANNEL:
            return entity_id
        return None

    async def _validate_preset_version_for_assignment(
        self,
        *,
        entity_type: AgentSessionEntity,
        entity_id: uuid.UUID,
        agent_preset_id: uuid.UUID | None,
        agent_preset_version_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        """Validate and return the pinned preset version for a session assignment."""
        logical_preset_id = self._resolve_logical_preset_id(
            entity_type=entity_type,
            entity_id=entity_id,
            agent_preset_id=agent_preset_id,
        )
        if logical_preset_id is None:
            if agent_preset_version_id is not None:
                raise TracecatNotFoundError(
                    "Cannot assign a preset version without a preset"
                )
            return None

        preset_service = AgentPresetService(self.session, self.role)
        if not await preset_service.get_preset(logical_preset_id):
            raise TracecatNotFoundError(
                f"Agent preset with ID '{logical_preset_id}' not found"
            )

        if agent_preset_version_id is None:
            return None

        version = await preset_service.resolve_agent_preset_version(
            preset_id=logical_preset_id,
            preset_version_id=agent_preset_version_id,
        )
        return version.id

    async def _resolve_agents_binding_for_preset_version_id(
        self, preset_version_id: uuid.UUID | None
    ) -> dict[str, Any] | None:
        """Resolve the normalized subagent binding for a pinned preset version."""
        if preset_version_id is None:
            return None

        preset_service = AgentPresetService(self.session, self.role)
        version = await preset_service.resolve_agent_preset_version(
            preset_version_id=preset_version_id
        )
        return ResolvedAgentsConfig.model_validate(version.agents).model_dump(
            mode="json"
        )

    async def get_session(
        self,
        session_id: uuid.UUID,
    ) -> AgentSession | None:
        """Get an agent session by ID.

        Only returns actual AgentSession records. Use get_legacy_chat()
        for legacy Chat records.

        Args:
            session_id: The session UUID.

        Returns:
            AgentSession model if found, None otherwise.
        """
        stmt = select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_legacy_chat(
        self,
        session_id: uuid.UUID,
    ) -> Chat | None:
        """Get a legacy Chat by ID.

        Args:
            session_id: The chat UUID.

        Returns:
            Chat model if found, None otherwise.
        """
        stmt = select(Chat).where(
            Chat.id == session_id,
            Chat.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def build_initial_artifact(
        self, agent_session: AgentSession
    ) -> Artifact | None:
        """Build the session's initial chat artifact, if supported."""
        entity_type = AgentSessionEntity(agent_session.entity_type)
        match entity_type:
            case AgentSessionEntity.CASE:
                stmt = select(Case).where(
                    Case.id == agent_session.entity_id,
                    Case.workspace_id == self.workspace_id,
                )
                result = await self.session.execute(stmt)
                case = result.scalar_one_or_none()
                if case is None:
                    return None
                return ArtifactAdapter.validate_python(
                    {
                        "type": "case",
                        "id": str(case.id),
                        "title": case.summary,
                        "severity": case.severity.value,
                        "status": case.status.value,
                    }
                )
            case AgentSessionEntity.WORKFLOW:
                stmt = select(Workflow).where(
                    Workflow.id == agent_session.entity_id,
                    Workflow.workspace_id == self.workspace_id,
                )
                result = await self.session.execute(stmt)
                workflow = result.scalar_one_or_none()
                if workflow is None:
                    return None
                return ArtifactAdapter.validate_python(
                    {
                        "type": "workflow",
                        "id": str(workflow.id),
                        "title": workflow.title,
                        "color": "#64748b",
                        "isPublished": workflow.status == "online",
                    }
                )
            case _:
                return None

    def list_artifacts(self, agent_session: AgentSession) -> list[Artifact]:
        """Return the persisted artifact projection for a session."""
        return artifact_projection.validate_artifacts(
            getattr(agent_session, "artifacts", [])
        )

    async def apply_artifact_side_effects(
        self,
        session_id: uuid.UUID,
        effects: Sequence[ArtifactSideEffect],
    ) -> list[Artifact]:
        """Persist artifact side effects onto the session projection."""
        if not effects:
            agent_session = await self.get_session(session_id)
            if agent_session is None:
                raise TracecatNotFoundError(f"Session {session_id} not found")
            return self.list_artifacts(agent_session)

        stmt = (
            select(AgentSession)
            .where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        agent_session = result.scalar_one_or_none()
        if agent_session is None:
            raise TracecatNotFoundError(f"Session {session_id} not found")

        current_artifacts = self.list_artifacts(agent_session)
        next_artifacts = artifact_projection.apply_artifact_side_effects(
            current_artifacts,
            effects,
        )
        agent_session.artifacts = artifact_projection.serialize_artifacts(
            next_artifacts
        )
        await self.session.commit()
        await self.session.refresh(agent_session)
        return self.list_artifacts(agent_session)

    async def remove_artifact(
        self,
        session_id: uuid.UUID,
        *,
        artifact_type: ArtifactType,
        artifact_id: str,
    ) -> list[Artifact]:
        """Remove one artifact from the persisted session projection."""
        stmt = (
            select(AgentSession)
            .where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        agent_session = result.scalar_one_or_none()
        if agent_session is None:
            raise TracecatNotFoundError(f"Session {session_id} not found")

        current_artifacts = self.list_artifacts(agent_session)
        next_artifacts = artifact_projection.remove_artifact(
            current_artifacts,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
        )
        agent_session.artifacts = artifact_projection.serialize_artifacts(
            next_artifacts
        )
        await self.session.commit()
        await self.session.refresh(agent_session)
        return self.list_artifacts(agent_session)

    async def is_legacy_session(self, session_id: uuid.UUID) -> bool:
        """Check if a session ID refers to a legacy Chat record.

        Args:
            session_id: The session/chat UUID.

        Returns:
            True if this is a legacy Chat, False otherwise.
        """
        chat = await self.get_legacy_chat(session_id)
        return chat is not None

    async def get_or_create_session(
        self,
        args: AgentSessionCreate,
        *,
        agents_binding: ResolvedAgentsConfig | None = None,
    ) -> tuple[AgentSession, bool]:
        """Get an existing session or create a new one.

        Looks up by session ID to find existing sessions.

        Args:
            args: Session creation parameters.

        Returns:
            Tuple of (AgentSession, created) where created is True if new.
        """
        if args.id is not None:
            existing = await self.get_session(args.id)
            if existing:
                return existing, False
        new_session = await self.create_session(args, agents_binding=agents_binding)
        return new_session, True

    async def list_sessions(
        self,
        *,
        created_by: UserID | None = None,
        filter_created_by_none: bool = False,
        entity_type: AgentSessionEntity | None = None,
        entity_id: uuid.UUID | None = None,
        exclude_entity_types: list[AgentSessionEntity] | None = None,
        parent_session_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[AgentSessionRead | ChatReadMinimal]:
        """List agent sessions and legacy chats for the workspace.

        Returns a merged list of AgentSession and legacy Chat records,
        sorted by created_at. Legacy chats have is_readonly=True.

        Args:
            created_by: Filter by user who created the session.
            filter_created_by_none: Filter to sessions without a user creator.
            entity_type: Filter by entity type.
            entity_id: Filter by entity ID.
            exclude_entity_types: Entity types to exclude from results.
            parent_session_id: Filter by parent session ID (for finding forked sessions).
            limit: Maximum number of results.

        Returns:
            List of AgentSessionRead or ChatReadMinimal (legacy, read-only).
        """
        # Query AgentSession table
        session_stmt = select(AgentSession).where(
            AgentSession.workspace_id == self.workspace_id
        )
        if created_by is not None:
            session_stmt = session_stmt.where(AgentSession.created_by == created_by)
        elif filter_created_by_none:
            session_stmt = session_stmt.where(AgentSession.created_by.is_(None))
        if entity_type is not None:
            session_stmt = session_stmt.where(
                AgentSession.entity_type == entity_type.value
            )
        if exclude_entity_types:
            session_stmt = session_stmt.where(
                AgentSession.entity_type.notin_(
                    [et.value for et in exclude_entity_types]
                )
            )
        if entity_id is not None:
            session_stmt = session_stmt.where(AgentSession.entity_id == entity_id)
        if parent_session_id is not None:
            session_stmt = session_stmt.where(
                AgentSession.parent_session_id == parent_session_id
            )
        # Bound query cost at the database layer; we still merge+sort below.
        session_stmt = session_stmt.order_by(AgentSession.created_at.desc()).limit(
            limit
        )

        session_result = await self.session.execute(session_stmt)
        sessions = list(session_result.scalars().all())

        legacy_chats: list[Chat] = []
        if parent_session_id is None and not filter_created_by_none:
            # Query legacy Chat table
            chat_stmt = select(Chat).where(Chat.workspace_id == self.workspace_id)
            if created_by is not None:
                chat_stmt = chat_stmt.where(Chat.user_id == created_by)
            if entity_type is not None:
                chat_stmt = chat_stmt.where(Chat.entity_type == entity_type.value)
            if exclude_entity_types:
                chat_stmt = chat_stmt.where(
                    Chat.entity_type.notin_([et.value for et in exclude_entity_types])
                )
            if entity_id is not None:
                chat_stmt = chat_stmt.where(Chat.entity_id == entity_id)
            # Bound query cost at the database layer; we still merge+sort below.
            chat_stmt = chat_stmt.order_by(Chat.created_at.desc()).limit(limit)

            chat_result = await self.session.execute(chat_stmt)
            legacy_chats = list(chat_result.scalars().all())

        # Convert and merge
        items: list[AgentSessionRead | ChatReadMinimal] = []

        for s in sessions:
            items.append(AgentSessionRead.model_validate(s, from_attributes=True))

        for c in legacy_chats:
            # ChatReadMinimal has is_readonly=True by default
            items.append(ChatReadMinimal.model_validate(c, from_attributes=True))

        # Sort by created_at descending and apply limit
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:limit]

    @audit_log(resource_type="agent_session", action="update")
    async def update_session(
        self,
        agent_session: AgentSession,
        *,
        params: AgentSessionUpdate,
    ) -> AgentSession:
        """Update an agent session.

        Args:
            agent_session: The AgentSession model to update.
            params: Fields to update.

        Returns:
            The updated AgentSession.
        """
        set_fields = params.model_dump(exclude_unset=True)
        preset_id_updated = "agent_preset_id" in set_fields
        version_id_updated = "agent_preset_version_id" in set_fields
        requested_preset_id = set_fields.pop(
            "agent_preset_id", agent_session.agent_preset_id
        )
        requested_version_id = set_fields.pop(
            "agent_preset_version_id", agent_session.agent_preset_version_id
        )
        if "mcp_integrations" in set_fields:
            await self._validate_session_mcp_integrations(
                set_fields["mcp_integrations"]
            )

        if preset_id_updated or version_id_updated:
            try:
                entity_type = AgentSessionEntity(agent_session.entity_type)
            except (TypeError, ValueError) as e:
                raise TracecatValidationError(
                    "Cannot update preset assignment for a session with an invalid entity type"
                ) from e
            logical_preset_id = self._resolve_logical_preset_id(
                entity_type=entity_type,
                entity_id=agent_session.entity_id,
                agent_preset_id=requested_preset_id,
            )
            if logical_preset_id is None:
                agent_session.agent_preset_id = None
                agent_session.agent_preset_version_id = None
                agent_session.agents_binding = None
            else:
                if preset_id_updated and (
                    requested_preset_id != agent_session.agent_preset_id
                ):
                    pinned_version_id = (
                        await self._validate_preset_version_for_assignment(
                            entity_type=entity_type,
                            entity_id=agent_session.entity_id,
                            agent_preset_id=requested_preset_id,
                            agent_preset_version_id=(
                                requested_version_id if version_id_updated else None
                            ),
                        )
                    )
                elif version_id_updated and requested_version_id is None:
                    pinned_version_id = None
                elif version_id_updated:
                    pinned_version_id = (
                        await self._validate_preset_version_for_assignment(
                            entity_type=entity_type,
                            entity_id=agent_session.entity_id,
                            agent_preset_id=requested_preset_id,
                            agent_preset_version_id=requested_version_id,
                        )
                    )
                else:
                    pinned_version_id = requested_version_id
                agent_session.agent_preset_id = logical_preset_id
                agent_session.agent_preset_version_id = pinned_version_id
                agent_session.agents_binding = (
                    await self._resolve_agents_binding_for_preset_version_id(
                        pinned_version_id
                    )
                )

        # Update remaining fields if provided
        for field, value in set_fields.items():
            setattr(agent_session, field, value)
        self.session.add(agent_session)
        await self.session.commit()
        await self.session.refresh(agent_session)

        return agent_session

    @audit_log(resource_type="agent_session", action="delete")
    async def delete_session(
        self,
        agent_session: AgentSession,
    ) -> None:
        """Delete an agent session and its history.

        Args:
            agent_session: The AgentSession model to delete.
        """
        await self.session.delete(agent_session)
        await self.session.commit()

    async def update_last_stream_id(
        self,
        agent_session: AgentSession,
        last_stream_id: str | None,
    ) -> AgentSession:
        """Update the last stream ID for an agent session.

        Args:
            agent_session: The agent session to update.
            last_stream_id: The Redis stream ID to store, or None to clear it.

        Returns:
            The updated AgentSession.
        """
        agent_session.last_stream_id = last_stream_id
        self.session.add(agent_session)
        await self.session.commit()
        await self.session.refresh(agent_session)
        return agent_session

    async def finalize_turn(
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> None:
        """Clear the active-turn pointers at terminal (compare-and-clear).

        Nulls ``curr_run_id`` and ``active_stream_id`` only while the session
        still points at ``run_id``. The compare guards against a newer turn that
        already overwrote these pointers: a stale terminal must not clear the
        live turn's state. Nulling ``curr_run_id`` is what flips the mid-turn DB
        filter off (final rows become visible) and makes reconnect resolve to
        NONE -> 204.
        """
        stmt = (
            update(AgentSession)
            .where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.curr_run_id == run_id,
            )
            .values(curr_run_id=None, active_stream_id=None)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def append_cancelled_marker(
        self,
        session_id: uuid.UUID,
        *,
        reason: str | None = None,
        interrupted_tool_call_ids: list[str] | None = None,
        curr_run_id: uuid.UUID | None = None,
    ) -> None:
        """Persist a turn-cancelled marker row into the session history.

        The marker renders as the "stopped by user" divider in the chat
        timeline after a reload; the live turn gets the equivalent signal from
        the ``cancelled`` stream event. It is not an SDK transcript line, so
        history hydration must skip it (see ``get_session_history_data``).

        ``interrupted_tool_call_ids`` carries the tool calls the interrupt
        aborted mid-flight so reloads can render them as "interrupted" instead
        of surfacing SDK abort artifacts as tool errors.

        ``curr_run_id`` pins the marker to the cancelled run. Callers that
        know their run id (the workflow) must pass it: the session row's
        ``curr_run_id`` may already point at a newer turn by the time the
        cancelled workflow finalizes, which would tag the marker to the wrong
        run and hide it while that turn is mid-flight.
        """
        agent_session = await self.get_session(session_id)
        if agent_session is None:
            raise TracecatNotFoundError(f"Session with ID {session_id} not found")

        content: dict[str, Any] = {
            "type": "cancelled",
            "reason": reason or "user_cancel",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        if interrupted_tool_call_ids:
            content["tool_call_ids"] = list(interrupted_tool_call_ids)
        # Tag with the active run id like other mid-turn rows so the marker
        # stays hidden from DB reloads until terminal cleanup reveals it
        # together with the rest of the turn.
        self.session.add(
            AgentSessionHistory(
                session_id=session_id,
                workspace_id=self.workspace_id,
                content=content,
                kind=MessageKind.CANCELLED.value,
                curr_run_id=curr_run_id
                if curr_run_id is not None
                else agent_session.curr_run_id,
            )
        )
        await self.session.commit()

    async def clear_active_turn(
        self,
        session_id: uuid.UUID,
        *,
        expected_stream_id: uuid.UUID,
    ) -> None:
        """Clear active-turn pointers on turn-startup failure (compare-and-clear).

        The workflow never started, so we drop the pointers optimistically
        written by ``run_turn``. Compare on ``active_stream_id`` (minted per turn
        at the HTTP layer) so a newer turn that already overwrote these pointers
        between our optimistic write and this cleanup is not clobbered: turns are
        not serialized, so a concurrent turn can pin its own pointers in that
        window. Only clear while the session still points at our stream id.
        """
        stmt = (
            update(AgentSession)
            .where(
                AgentSession.id == session_id,
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.active_stream_id == expected_stream_id,
            )
            .values(curr_run_id=None, active_stream_id=None)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    # =========================================================================
    # Session History Management (for Claude SDK session persistence)
    # =========================================================================

    async def load_session_history(
        self,
        session_id: uuid.UUID,
    ) -> SessionHistoryData | None:
        """Load session history for resume.

        Reconstructs the SDK session JSONL from stored history entries.
        Returns None if no history exists or no sdk_session_id is set.

        For forked sessions (with parent_session_id), loads the parent's history
        and sets is_fork=True so the runtime uses fork_session=True with the SDK.

        The sdk_session_id is stored on the AgentSession model (not in the
        JSONL content) to keep the history entries pristine for SDK resume.

        Args:
            session_id: The session UUID.

        Returns:
            SessionHistoryData with sdk_session_id and reconstructed JSONL,
            or None if no history found or sdk_session_id not set.
        """
        # First get the AgentSession to check for fork and retrieve sdk_session_id
        agent_session = await self.get_session(session_id)
        if agent_session is None:
            return None

        # For forked sessions, only fork on the first turn (when child has no sdk_session_id yet)
        # On subsequent turns, child has its own sdk_session_id and should resume normally
        is_fork = False
        if (
            agent_session.parent_session_id is not None
            and agent_session.sdk_session_id is None
        ):
            is_fork = True
            parent_session = await self.get_session(agent_session.parent_session_id)
            if parent_session is None:
                logger.warning(
                    "Forked session references non-existent parent",
                    session_id=session_id,
                    parent_session_id=agent_session.parent_session_id,
                )
                return None
            # Use parent's sdk_session_id and history
            source_session = parent_session
            source_session_id = agent_session.parent_session_id
        else:
            source_session = agent_session
            source_session_id = session_id

        sdk_session_id = source_session.sdk_session_id
        if not sdk_session_id:
            logger.debug(
                "No sdk_session_id on session (new session or legacy)",
                session_id=source_session_id,
            )
            return None

        # Load history entries from source session
        stmt = (
            select(AgentSessionHistory)
            .where(
                AgentSessionHistory.session_id == source_session_id,
            )
            .order_by(AgentSessionHistory.surrogate_id)
        )
        result = await self.session.execute(stmt)
        history_entries = list(result.scalars().all())

        if not history_entries:
            logger.warning(
                "sdk_session_id set but no history entries",
                session_id=source_session_id,
                sdk_session_id=sdk_session_id,
            )
            return None

        # Reconstruct JSONL from model-visible history entries. Internal rows are
        # stored for debugging/UI filtering but should not be fed back into Claude
        # on later resumes.
        lines = []
        included_uuids: set[str] = set()
        internal_uuids: set[str] = set()
        last_visible_uuid: str | None = None
        for entry in history_entries:
            content = orjson.loads(orjson.dumps(entry.content))
            if not isinstance(content, dict):
                continue

            line_uuid = session_line_uuid(content)
            if entry.kind == MessageKind.INTERNAL.value:
                if line_uuid is not None:
                    internal_uuids.add(line_uuid)
                continue

            # Cancelled markers are UI-only timeline rows, not SDK transcript
            # lines - feeding one into the rebuilt JSONL would corrupt resume.
            if entry.kind == MessageKind.CANCELLED.value:
                continue

            if is_continuation_control_artifact(content, internal_uuids):
                if line_uuid is not None:
                    internal_uuids.add(line_uuid)
                continue

            parent_uuid = content.get("parentUuid")
            if (
                isinstance(parent_uuid, str)
                and parent_uuid not in included_uuids
                and last_visible_uuid is not None
            ):
                content["parentUuid"] = last_visible_uuid

            if line_uuid is not None:
                included_uuids.add(line_uuid)
                last_visible_uuid = line_uuid

            line = orjson.dumps(content).decode("utf-8")
            lines.append(line)

        if not lines:
            logger.warning(
                "sdk_session_id set but no model-visible history entries",
                session_id=source_session_id,
                sdk_session_id=sdk_session_id,
            )
            return None

        sdk_session_data = "\n".join(lines)

        return SessionHistoryData(
            sdk_session_id=sdk_session_id,
            sdk_session_data=sdk_session_data,
            is_fork=is_fork,
        )

    async def get_session_history(
        self,
        session_id: uuid.UUID,
    ) -> list[AgentSessionHistory]:
        """Get all history entries for a session.

        Args:
            session_id: The session UUID.

        Returns:
            List of AgentSessionHistory entries ordered by surrogate_id.
        """
        stmt = (
            select(AgentSessionHistory)
            .where(
                AgentSessionHistory.session_id == session_id,
            )
            .order_by(AgentSessionHistory.surrogate_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def is_first_prompt_for_session(self, session_id: uuid.UUID) -> bool:
        """Check whether this session has any persisted local history yet."""
        stmt = (
            select(AgentSessionHistory.id)
            .where(
                AgentSessionHistory.workspace_id == self.workspace_id,
                AgentSessionHistory.session_id == session_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is None

    async def has_pending_approvals(self, session_id: uuid.UUID) -> bool:
        """Return whether the session has pending approval decisions."""
        stmt = (
            select(Approval.id)
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id == session_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _pending_approval_tool_call_ids(self, session_id: uuid.UUID) -> set[str]:
        """Return pending approval tool-call IDs for one session."""
        stmt = select(Approval.tool_call_id).where(
            Approval.workspace_id == self.workspace_id,
            Approval.session_id == session_id,
            Approval.status == ApprovalStatus.PENDING,
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def _settled_approval_decisions(
        self, session_id: uuid.UUID
    ) -> dict[str, PersistedApprovalDecision | None]:
        """Return already-decided approvals so retries can be reconciled."""
        stmt = select(Approval.tool_call_id, Approval.decision).where(
            Approval.workspace_id == self.workspace_id,
            Approval.session_id == session_id,
            Approval.status != ApprovalStatus.PENDING,
        )
        result = await self.session.execute(stmt)
        return {row.tool_call_id: row.decision for row in result}

    async def _apply_submitted_approval_decisions(
        self,
        *,
        session_id: uuid.UUID,
        approval_map: Mapping[str, ApprovalResult],
        decision_metadata: Mapping[str, ApprovalDecisionMetadata],
    ) -> None:
        """Persist accepted approval decisions before the full set resumes."""
        if not approval_map:
            return

        approved_by = await self._existing_user_id(self.role.user_id)
        rows: list[_DecisionRow] = []
        for tool_call_id, approval_result in approval_map.items():
            fields = _approval_decision_fields(
                approval_result,
                approved_by=approved_by,
                decision_metadata=decision_metadata.get(tool_call_id),
            )
            rows.append(
                _DecisionRow(
                    tool_call_id=tool_call_id,
                    status=fields.status,
                    reason=fields.reason,
                    decision=fields.decision,
                    approved_at=fields.approved_at,
                )
            )

        # Column types mirror the Approval model so values bind directly.
        decisions = (
            values(
                column("tool_call_id", String),
                column("status", APPROVAL_STATUS_ENUM),
                column("reason", String),
                column("decision", JSONB),
                column("approved_at", TIMESTAMP(timezone=True)),
                name="decisions",
            )
            .data(rows)
            .alias()
        )

        stmt = (
            update(Approval)
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id == session_id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.tool_call_id == decisions.c.tool_call_id,
            )
            .values(
                status=decisions.c.status,
                reason=decisions.c.reason,
                decision=decisions.c.decision,
                approved_by=literal(approved_by, UUID),
                approved_at=decisions.c.approved_at,
                # Core-level bulk UPDATE does not fire the mapper `onupdate`.
                updated_at=func.now(),
            )
            .returning(Approval.tool_call_id)
            # "fetch" expires the updated rows in the identity map so
            # `_emit_approval_idle_segment` re-reads them fresh. It reuses the
            # RETURNING above, so this costs no extra query. "evaluate" cannot
            # replay a criteria that joins a SQL-only VALUES clause.
            .execution_options(synchronize_session="fetch")
        )
        result = await self.session.execute(stmt)
        updated = set(result.scalars().all())

        # Validation admits only pending IDs, so a miss here means the row was
        # decided by a concurrent submitter in between, or is missing entirely.
        if skipped := set(approval_map) - updated:
            logger.warning(
                "Accepted approval decisions were missing or no longer pending",
                session_id=str(session_id),
                tool_call_ids=sorted(skipped),
            )

        await self.session.commit()

    async def _existing_user_id(self, user_id: uuid.UUID | None) -> uuid.UUID | None:
        """Return ``user_id`` only when it can satisfy Approval.approved_by."""
        if user_id is None:
            return None
        stmt = select(User).where(cast(Any, User.id) == user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        return user.id if user else None

    async def _approval_stream_items(
        self,
        *,
        session_id: uuid.UUID,
        tool_call_ids: Sequence[str],
    ) -> list[ToolCallContent]:
        """Return the active approval batch as stream items for UI replay.

        Only pending approvals plus the just-submitted ``tool_call_ids`` are
        replayed. Terminal approvals from earlier turns must stay out of the
        payload: the client drops an entire ``data-approval-request`` part when
        any contained tool call already has a terminal tool state, which would
        hide the still-pending cards.
        """
        stmt = (
            select(Approval)
            .where(
                Approval.workspace_id == self.workspace_id,
                Approval.session_id == session_id,
                or_(
                    Approval.status == ApprovalStatus.PENDING,
                    Approval.tool_call_id.in_(tool_call_ids),
                ),
            )
            .order_by(Approval.created_at, Approval.id)
        )
        result = await self.session.execute(stmt)
        return [
            ToolCallContent(
                id=approval.tool_call_id,
                name=approval.tool_name,
                input=approval.tool_call_args or {},
                status=cast(ApprovalStreamStatus, approval.status.value),
                decision=approval.decision,
                reason=approval.reason,
            )
            for approval in result.scalars().all()
        ]

    async def _emit_approval_idle_segment(
        self,
        *,
        session_id: uuid.UUID,
        stream_id: uuid.UUID | None,
        tool_call_ids: Sequence[str],
    ) -> None:
        """Emit the latest approval state and a non-terminal Redis boundary."""
        if self.workspace_id is None:
            return
        stream = await AgentStream.new(
            workspace_id=self.workspace_id,
            session_id=session_id,
            stream_id=stream_id,
        )
        if approval_items := await self._approval_stream_items(
            session_id=session_id, tool_call_ids=tool_call_ids
        ):
            await stream.append(
                UnifiedStreamEvent.approval_request_event(approval_items).to_dict()
            )
        await stream.finish_idle_segment()

    @staticmethod
    def _approval_submission_key(
        *,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        tool_call_ids: Sequence[str],
    ) -> str:
        digest = hashlib.sha256(
            ",".join(sorted(tool_call_ids)).encode("utf-8")
        ).hexdigest()[:16]
        return f"agent-approval-submit:{workspace_id}:{session_id}:{run_id}:{digest}"

    async def _existing_approval_continuation_attempt(
        self,
        agent_session: AgentSession,
        *,
        submission_key: str,
    ) -> ApprovalContinuationAttempt | None:
        """Return the installed attempt for this approval submission, if any."""
        stream_id = agent_session.active_stream_id
        if stream_id is None:
            return None

        stream = await AgentStream.new(
            session_id=agent_session.id,
            workspace_id=self.workspace_id,
            stream_id=stream_id,
        )
        marker = await stream.approval_continuation_marker()
        if marker is None or marker.submission_key != submission_key:
            return None
        return ApprovalContinuationAttempt(
            stream=stream,
            stream_id=stream_id,
            previous_stream_id=marker.previous_stream_id,
        )

    async def _prepare_approval_continuation_attempt(
        self,
        *,
        agent_session: AgentSession,
        curr_run_id: uuid.UUID,
        submission_key: str,
    ) -> ApprovalContinuationAttempt:
        """Reuse an installed attempt or atomically install a fresh stream."""
        if attempt := await self._existing_approval_continuation_attempt(
            agent_session,
            submission_key=submission_key,
        ):
            return attempt

        previous_stream_id = agent_session.active_stream_id
        stream_id = uuid.uuid4()
        stream = await AgentStream.new(
            session_id=agent_session.id,
            workspace_id=self.workspace_id,
            stream_id=stream_id,
        )
        await stream.mark_approval_continuation(
            submission_key=submission_key,
            previous_stream_id=previous_stream_id,
        )

        previous_stream_filter = (
            AgentSession.active_stream_id.is_(None)
            if previous_stream_id is None
            else AgentSession.active_stream_id == previous_stream_id
        )
        stmt = (
            update(AgentSession)
            .where(
                AgentSession.id == agent_session.id,
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.curr_run_id == curr_run_id,
                previous_stream_filter,
            )
            .values(active_stream_id=stream_id)
            .returning(AgentSession.active_stream_id)
        )
        try:
            result = await self.session.execute(stmt)
            installed_stream_id = result.scalar_one_or_none()
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        if installed_stream_id == stream_id:
            agent_session.active_stream_id = stream_id
            return ApprovalContinuationAttempt(
                stream=stream,
                stream_id=stream_id,
                previous_stream_id=previous_stream_id,
            )

        # Another request won the compare-and-swap. This candidate was never
        # published, so remove it and reuse the installed attempt instead.
        with contextlib.suppress(Exception):
            await stream.clear_buffer()
        await self.session.refresh(agent_session)
        if attempt := await self._existing_approval_continuation_attempt(
            agent_session,
            submission_key=submission_key,
        ):
            return attempt
        raise TracecatConflictError(
            "Approval continuation changed while decisions were being submitted"
        )

    async def _rollback_rejected_approval_continuation(
        self,
        *,
        agent_session: AgentSession,
        curr_run_id: uuid.UUID,
        attempt: ApprovalContinuationAttempt,
    ) -> None:
        """Close a rejected attempt and compare-and-restore its prior pointer."""
        try:
            await attempt.stream.done()
        except Exception as exc:
            logger.warning(
                "Failed to close rejected approval continuation stream",
                session_id=str(agent_session.id),
                stream_id=str(attempt.stream_id),
                error=str(exc),
            )

        stmt = (
            update(AgentSession)
            .where(
                AgentSession.id == agent_session.id,
                AgentSession.workspace_id == self.workspace_id,
                AgentSession.curr_run_id == curr_run_id,
                AgentSession.active_stream_id == attempt.stream_id,
            )
            .values(active_stream_id=attempt.previous_stream_id)
            .returning(AgentSession.id)
        )
        try:
            result = await self.session.execute(stmt)
            restored_session_id = result.scalar_one_or_none()
            await self.session.commit()
            if restored_session_id is None:
                await self.session.refresh(agent_session)
                logger.info(
                    "Skipped stale approval continuation rollback",
                    session_id=str(agent_session.id),
                    stream_id=str(attempt.stream_id),
                    active_stream_id=str(agent_session.active_stream_id),
                )
            else:
                agent_session.active_stream_id = attempt.previous_stream_id
        except SQLAlchemyError as exc:
            await self.session.rollback()
            logger.warning(
                "Failed to restore stream pointer after rejected approval continuation",
                session_id=str(agent_session.id),
                stream_id=str(attempt.stream_id),
                error=str(exc),
            )

    async def auto_title_session_on_first_prompt(
        self,
        agent_session: AgentSession,
        user_prompt: str,
        *,
        expected_title: str,
    ) -> None:
        """Best-effort auto-title on first prompt via direct PydanticAI call."""
        prompt = user_prompt.strip()
        entity_type = agent_session.entity_type
        old_title = expected_title

        if agent_session.title != expected_title:
            logger.info(
                "session_auto_title_skip",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=len(prompt),
                old_title_length=len(old_title),
                new_title_length=len(agent_session.title),
                reason="title_changed_since_scheduling",
            )
            return

        if not prompt:
            logger.info(
                "session_auto_title_skip",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=0,
                old_title_length=len(old_title),
                new_title_length=0,
                reason="empty_prompt",
            )
            return

        logger.info(
            "session_auto_title_attempt",
            session_id=str(agent_session.id),
            entity_type=entity_type,
            prompt_length=len(prompt),
            old_title_length=len(old_title),
        )

        try:
            service_role = self.role.model_copy(
                update={
                    "type": "service",
                    "service_id": AUTO_TITLE_SERVICE_ID,
                    "scopes": SERVICE_PRINCIPAL_SCOPES.get(
                        AUTO_TITLE_SERVICE_ID,
                        frozenset(),
                    ),
                }
            )
            new_title = await generate_session_title(
                user_prompt=prompt,
                session=self.session,
                role=service_role,
            )
        except (
            LLMCompletionError,
            SecretNotFoundError,
            TracecatNotFoundError,
            TracecatValidationError,
            ValueError,
        ) as e:
            logger.warning(
                "session_auto_title_failure",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=len(prompt),
                old_title_length=len(old_title),
                error=str(e),
                error_type=type(e).__name__,
            )
            return

        if not new_title:
            logger.info(
                "session_auto_title_skip",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=len(prompt),
                old_title_length=len(old_title),
                new_title_length=0,
                reason="generation_failed_or_empty",
            )
            return

        if new_title == old_title:
            logger.info(
                "session_auto_title_skip",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=len(prompt),
                old_title_length=len(old_title),
                new_title_length=len(new_title),
                reason="title_unchanged",
            )
            return

        try:
            result = await self.session.execute(
                update(AgentSession)
                .where(
                    AgentSession.id == agent_session.id,
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.title == old_title,
                )
                .values(title=new_title)
                .returning(AgentSession.id)
            )
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            logger.warning(
                "session_auto_title_failure",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=len(prompt),
                old_title_length=len(old_title),
                new_title_length=len(new_title),
                error=str(e),
                error_type=type(e).__name__,
            )
            return

        updated_session_id = result.scalar_one_or_none()
        if updated_session_id is not None:
            agent_session.title = new_title
            logger.info(
                "session_auto_title_success",
                session_id=str(agent_session.id),
                entity_type=entity_type,
                prompt_length=len(prompt),
                old_title_length=len(old_title),
                new_title_length=len(new_title),
            )
            return

        await self.session.refresh(agent_session)
        logger.info(
            "session_auto_title_skip",
            session_id=str(agent_session.id),
            entity_type=entity_type,
            prompt_length=len(prompt),
            old_title_length=len(old_title),
            new_title_length=len(agent_session.title),
            reason="compare_and_set_guard_failed",
        )

    # =========================================================================
    # Chat / Message Turn Operations
    # =========================================================================

    async def run_turn(
        self,
        session_id: uuid.UUID,
        request: ChatRequest | ContinueRunRequest | BasicChatRequest,
        *,
        active_stream_id: uuid.UUID | None = None,
        is_first_prompt: bool | None = None,
    ) -> ChatResponse | None:
        """Run a session turn by spawning a DurableAgentWorkflow.

        This method prepares the chat turn and spawns a DurableAgentWorkflow
        on the agent-action-queue for durable execution.

        Args:
            session_id: The ID of the session.
            active_stream_id: Per-turn stream id minted by the HTTP layer. Pinned
                into the workflow input and onto the session row so the producer
                and reader share the same per-turn Redis key. Defaults to a freshly
                minted id for callers that don't manage their own stream.
            is_first_prompt: Whether the session had no history before this turn.
                When omitted, the service queries history for direct callers.
            request: Either a ChatRequest (start) or ContinueRunRequest (continue).

        Returns:
            ChatResponse when starting a new turn or continuing with approvals
            (the continuation response carries the rotated ``active_stream_id``).
            None only for a no-op continuation whose approvals are already
            resolved.

        Raises:
            TracecatNotFoundError: If the session is not found.
            ValueError: If the request/entity type is unsupported.
        """
        from tracecat_ee.agent.types import AgentWorkflowID
        from tracecat_ee.agent.workflows.durable import (
            AgentWorkflowArgs,
            DurableAgentWorkflow,
        )

        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required")

        agent_session = await self.validate_turn_request(
            session_id=session_id,
            request=request,
        )

        # Parse request to extract user prompt
        user_prompt: str | None = None
        request_instructions: str | None = None
        is_continuation = False

        match request:
            case ContinueRunRequest():
                is_continuation = True
            case VercelChatRequest(message=ui_message):
                [message] = tracecat.agent.adapter.vercel.convert_ui_message(ui_message)
                match message:
                    case ModelRequest(parts=[UserPromptPart(content=content)]):
                        match content:
                            case str(s):
                                user_prompt = s
                            case list(l):
                                user_prompt = "\n".join(str(item) for item in l)
                            case _:
                                raise ValueError(f"Unsupported user prompt: {content}")
                    case _:
                        raise ValueError(f"Unsupported message: {message}")
            case BasicChatRequest(message=prompt, instructions=instructions):
                user_prompt = prompt
                request_instructions = instructions
            case _:
                raise ValueError(f"Unsupported request type: {type(request)}")

        if user_prompt is not None:
            logger.info("Received user prompt", prompt_length=len(user_prompt))

        # Handle continuation (approval submission) vs new turn
        if is_continuation and isinstance(request, ContinueRunRequest):
            return await self._continue_with_approvals(session_id, request)

        # Titling eligibility must be decided before the workflow spawns: the
        # executor streams history rows mid-turn, so checking afterwards would
        # race. Placeholder titles ("Chat 1", "Slack thread", ...) vary by
        # surface, so first-prompt history is the signal, not the title text.
        if is_first_prompt is None:
            is_first_prompt = await self.is_first_prompt_for_session(session_id)
        should_auto_title = user_prompt is not None and is_first_prompt
        # Snapshot here so the detached task cannot overwrite a mid-flight rename.
        expected_title = agent_session.title

        # Build agent config and spawn workflow for new turn
        async with self._build_agent_config(agent_session) as agent_config:
            if request_instructions:
                agent_config = replace(
                    agent_config,
                    instructions="\n\n".join(
                        part
                        for part in (agent_config.instructions, request_instructions)
                        if part
                    ),
                )
            if agent_config.tool_approvals:
                await check_entitlement(
                    self.session, self.role, Entitlement.AGENT_ADDONS
                )
            run_id = uuid.uuid4()
            # Per-turn stream id: use the HTTP-minted id when provided, else mint
            # one. Pinned into the workflow input so the worker producer writes to
            # the same per-turn Redis key the reader joins (immune to the
            # stale-turn overwrite race).
            stream_id = active_stream_id or uuid.uuid4()

            args = RunAgentArgs(
                user_prompt=user_prompt or "",
                session_id=session_id,
                active_stream_id=stream_id,
                curr_run_id=run_id,
                config=agent_config,
            )

            client = await get_temporal_client()
            workflow_id = AgentWorkflowID(run_id)

            workflow_args = AgentWorkflowArgs(
                role=self.role,
                agent_args=args,
                title=agent_session.title,
                entity_type=AgentSessionEntity(agent_session.entity_type),
                entity_id=agent_session.entity_id,
                tools=agent_session.tools,
                agent_preset_id=agent_session.agent_preset_id,
                agent_preset_version_id=agent_session.agent_preset_version_id,
            )

            # Pin run_id (approval lookups) and the per-turn stream id before
            # launching the workflow. Clear last_error in the same transaction
            # that records the new run id: create_session_activity also clears
            # it, but that runs inside the workflow on the agent worker. If the
            # worker is queued/unavailable after a retry, the stale last_error
            # would otherwise make _resolve_live_statuses report the old failure
            # for a turn that is actually starting.
            agent_session.curr_run_id = run_id
            agent_session.active_stream_id = stream_id
            agent_session.last_error = None
            self.session.add(agent_session)
            await self.session.commit()

            logger.info(
                "Spawning DurableAgentWorkflow",
                workflow_id=str(workflow_id),
                session_id=str(session_id),
                run_id=str(run_id),
                entity_type=agent_session.entity_type,
                entity_id=str(agent_session.entity_id)
                if agent_session.entity_id
                else None,
                task_queue=config.TRACECAT__AGENT_QUEUE,
            )

            await client.start_workflow(
                DurableAgentWorkflow.run,
                workflow_args,
                id=str(workflow_id),
                task_queue=config.TRACECAT__AGENT_QUEUE,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                priority=Priority(priority_key=1),
                search_attributes=self._build_direct_agent_search_attributes(
                    session_id
                ),
            )

            # Spawn after the workflow starts so a rejected/failed turn never
            # renames the session.
            if should_auto_title and user_prompt is not None:
                task = asyncio.create_task(
                    _auto_title_session(
                        session_id=session_id,
                        user_prompt=user_prompt,
                        expected_title=expected_title,
                        role=self.role,
                    )
                )
                _background_tasks.add(task)
                task.add_done_callback(
                    partial(_finalize_auto_title_task, session_id=session_id)
                )

        # Return ChatResponse with session_id for streaming. Surface run_id so the
        # HTTP layer builds the stable bubble id from the value we just minted
        # rather than re-reading curr_run_id, which finalize_turn may clear before
        # the post-run refresh on a fast turn.
        stream_url = f"/api/agent/sessions/{session_id}/stream"
        return ChatResponse(
            stream_url=stream_url, chat_id=session_id, curr_run_id=run_id
        )

    async def get_turn_lifecycle(
        self, agent_session: AgentSession
    ) -> TurnLifecycleResult:
        """Resolve the live turn lifecycle from Temporal (cold reconnect path).

        Temporal owns lifecycle - we never cache it in the DB. Returns the
        decision plus the run id used to compute it (None when there is no
        current run). On any describe error we fall back to FAILED so a
        reconnecting client gets a terminal frame instead of hanging.
        """
        from temporalio.client import WorkflowExecutionStatus
        from tracecat_ee.agent.types import AgentWorkflowID
        from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

        curr_run_id = agent_session.curr_run_id
        if curr_run_id is None:
            return TurnLifecycleResult(TurnLifecycle.NONE, None)

        client = await get_temporal_client()
        handle = client.get_workflow_handle_for(
            DurableAgentWorkflow.run, AgentWorkflowID(curr_run_id)
        )
        try:
            description = await handle.describe()
        except RPCError:
            # Workflow history already gone / never started -> treat as failed so
            # the client receives a terminal frame and refetches DB history.
            logger.warning(
                "Failed to describe agent workflow for reconnect",
                session_id=str(agent_session.id),
                run_id=str(curr_run_id),
            )
            return TurnLifecycleResult(TurnLifecycle.FAILED, curr_run_id)

        match description.status:
            case (
                WorkflowExecutionStatus.RUNNING
                | WorkflowExecutionStatus.CONTINUED_AS_NEW
            ):
                # CONTINUED_AS_NEW is not currently reachable - DurableAgentWorkflow
                # loops turns internally rather than calling continue_as_new - but
                # treat it as still-running (not failed) for consistency with how
                # the inbox provider classifies Temporal workflow statuses.
                return TurnLifecycleResult(TurnLifecycle.RUNNING, curr_run_id)
            case WorkflowExecutionStatus.COMPLETED:
                return TurnLifecycleResult(TurnLifecycle.COMPLETED, curr_run_id)
            case WorkflowExecutionStatus.CANCELED:
                return TurnLifecycleResult(TurnLifecycle.CANCELLED, curr_run_id)
            case _:
                # FAILED | TERMINATED | TIMED_OUT
                return TurnLifecycleResult(TurnLifecycle.FAILED, curr_run_id)

    async def _is_attachable_continuation(self, agent_session: AgentSession) -> bool:
        """Probe whether the active stream is an open approval continuation.

        Approval-paused runs are still RUNNING in Temporal. A freshly rotated
        continuation is the exception: its marker is written before the stream ID
        is committed, so reconnects can safely wait on it while decisions finish
        committing.
        """
        if agent_session.active_stream_id is None:
            return False
        try:
            stream = await AgentStream.new(
                session_id=agent_session.id,
                workspace_id=self.workspace_id,
                stream_id=agent_session.active_stream_id,
            )
            return await stream.is_open_approval_continuation()
        except Exception as exc:
            logger.warning(
                "Failed to inspect approval continuation stream",
                session_id=str(agent_session.id),
                stream_id=str(agent_session.active_stream_id),
                error=str(exc),
            )
            return False

    async def get_stream_resume_state(
        self, agent_session: AgentSession
    ) -> StreamResumeState:
        """Resolve whether the session has a live stream a client can attach to."""
        lifecycle, curr_run_id = await self.get_turn_lifecycle(agent_session)
        has_live_stream = lifecycle is TurnLifecycle.RUNNING
        if has_live_stream and await self.has_pending_approvals(agent_session.id):
            has_live_stream = await self._is_attachable_continuation(agent_session)

        return StreamResumeState(
            lifecycle=lifecycle,
            curr_run_id=curr_run_id,
            active_stream_id=agent_session.active_stream_id,
            has_live_stream=has_live_stream,
        )

    async def validate_turn_request(
        self,
        session_id: uuid.UUID,
        request: ChatRequest | ContinueRunRequest | BasicChatRequest,
    ) -> AgentSession:
        """Assert a turn can start before mutating session or stream state."""
        agent_session = await self.get_session(session_id)
        if not agent_session:
            raise TracecatNotFoundError(f"Session with ID {session_id} not found")

        match request:
            case ContinueRunRequest():
                if agent_session.curr_run_id is None:
                    raise TracecatNotFoundError(
                        f"No active workflow run for session {session_id}"
                    )
                return agent_session
            case VercelChatRequest() | BasicChatRequest():
                if await self.has_pending_approvals(session_id):
                    raise ValueError(
                        "This session is waiting for approval decisions. "
                        "Submit all pending approvals before sending another message."
                    )
                return agent_session
            case _:
                raise ValueError(f"Unsupported request type: {type(request)}")

    def _validate_continuation_decisions(
        self,
        *,
        request: ContinueRunRequest,
        pending_tool_call_ids: set[str],
        settled_decisions: Mapping[str, PersistedApprovalDecision | None],
    ) -> _ValidatedContinuation:
        """Validate decisions against pending and already-settled approvals.

        Partial batches make resubmission normal: a decision that matches what
        is already persisted is dropped as a no-op, so only still-pending
        decisions reach the workflow.

        Raises:
            ValueError: On duplicate submitted IDs, or IDs that are neither
                pending nor already decided.
            TracecatConflictError: If a decision contradicts a settled one.
        """

        source = request.source
        submitted_tool_call_ids = [
            decision.tool_call_id for decision in request.decisions
        ]
        submitted_tool_call_id_set = set(submitted_tool_call_ids)
        if len(submitted_tool_call_ids) != len(submitted_tool_call_id_set):
            raise ValueError("Approval decisions contain duplicate tool call IDs")
        unexpected = sorted(
            submitted_tool_call_id_set
            - pending_tool_call_ids
            - settled_decisions.keys()
        )
        if not submitted_tool_call_id_set or unexpected:
            missing = sorted(pending_tool_call_ids - submitted_tool_call_id_set)
            raise ValueError(
                "Approval decisions do not match pending tool calls"
                f" (missing={missing}, unexpected={unexpected})"
            )

        approval_map: ApprovalMap = {}
        decision_metadata: dict[str, ApprovalDecisionMetadata] = {}
        for decision in request.decisions:
            approval_result: ApprovalResult
            if decision.action == "approve":
                approval_result = True
            elif decision.action == "override":
                approval_result = ToolApproved(
                    override_args=decision.override_args or {}
                )
            elif decision.action == "deny":
                approval_result = ToolDenied(
                    message=decision.reason or "Tool denied by user"
                )
            else:
                logger.warning(
                    "Unknown approval decision action; defaulting to deny",
                    action=decision.action,
                    tool_call_id=decision.tool_call_id,
                )
                approval_result = ToolDenied(
                    message=decision.reason or "Tool denied by user"
                )

            if decision.tool_call_id not in pending_tool_call_ids:
                # Already settled: identical resubmissions are dropped, but a
                # contradicting one must not read as accepted.
                if not _decision_matches_persisted(
                    approval_result, settled_decisions[decision.tool_call_id]
                ):
                    raise TracecatConflictError(
                        "Approval decision conflicts with a decision already"
                        f" recorded for tool call {decision.tool_call_id}"
                    )
                continue

            approval_map[decision.tool_call_id] = approval_result
            merged_metadata: ApprovalDecisionMetadata = {"source": source}
            if decision.metadata:
                merged_metadata.update(decision.metadata)
                merged_metadata["source"] = source
            decision_metadata[decision.tool_call_id] = merged_metadata

        return _ValidatedContinuation(
            approval_map=approval_map,
            decision_metadata=decision_metadata,
        )

    async def _submit_approval_update(
        self,
        *,
        agent_session: AgentSession,
        curr_run_id: uuid.UUID,
        attempt: ApprovalContinuationAttempt,
        validated: _ValidatedContinuation,
        handle: Any,
    ) -> bool:
        """Submit the Temporal update, preserving the attempt on ambiguous failure.

        Ambiguous transport failures leave the attempt intact so a retry reuses
        the same Temporal update id; definitive rejections roll it back.
        """
        from tracecat_ee.agent.workflows.durable import (
            DurableAgentWorkflow,
            WorkflowApprovalSubmission,
        )

        try:
            resumed = await handle.execute_update(
                DurableAgentWorkflow.set_approvals,
                WorkflowApprovalSubmission(
                    approvals=validated.approval_map,
                    approved_by=self.role.user_id,
                    decision_metadata=validated.decision_metadata or None,
                    new_stream_id=attempt.stream_id,
                ),
                id=f"set-approvals:{attempt.stream_id}",
            )
        except BaseException as exc:
            if isinstance(exc, Exception) and not isinstance(
                exc,
                (WorkflowUpdateRPCTimeoutOrCancelledError, RPCError),
            ):
                await self._rollback_rejected_approval_continuation(
                    agent_session=agent_session,
                    curr_run_id=curr_run_id,
                    attempt=attempt,
                )
            raise
        return resumed is not False

    async def _continue_with_approvals(
        self,
        session_id: uuid.UUID,
        request: ContinueRunRequest,
    ) -> ChatResponse | None:
        """Continue an agent workflow by submitting approval decisions.

        Two idempotency layers converge concurrent submissions (Slack <->
        inbox): the DB CAS on ``active_stream_id`` picks a single winning rotated
        stream, and the Temporal update id ``set-approvals:{stream_id}`` dedups
        server-side so duplicate submitters get the same result.

        Raises:
            TracecatNotFoundError: If no active session exists.
        """
        from tracecat_ee.agent.types import AgentWorkflowID
        from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

        agent_session = await self.get_session(session_id)
        if agent_session is None:
            raise TracecatNotFoundError(
                f"No active agent session found with ID {session_id}"
            )
        curr_run_id = agent_session.curr_run_id
        if curr_run_id is None:
            raise TracecatNotFoundError(
                f"No active workflow run for session {session_id}"
            )

        source: Literal["inbox", "slack"] = request.source

        # Idempotency path: resubmissions are normal for partial batches, so
        # reconcile against settled decisions instead of only pending ones.
        # A matching replay is a no-op; a contradicting one raises.
        pending_tool_call_ids = await self._pending_approval_tool_call_ids(session_id)
        settled_decisions = await self._settled_approval_decisions(session_id)

        validated = self._validate_continuation_decisions(
            request=request,
            pending_tool_call_ids=pending_tool_call_ids,
            settled_decisions=settled_decisions,
        )
        if not validated.approval_map:
            logger.info(
                "Ignoring approval continuation with no undecided approvals",
                session_id=str(session_id),
                run_id=str(curr_run_id),
                source=source,
            )
            return None

        # Resolve the workflow handle first. These operations do not mutate
        # continuation state, so failures here should not suppress a later retry.
        client = await get_temporal_client()
        workflow_id = AgentWorkflowID(curr_run_id)
        handle = client.get_workflow_handle_for(
            DurableAgentWorkflow.run,
            str(workflow_id),
        )

        logger.info(
            "Submitting approval decisions to workflow",
            workflow_id=str(workflow_id),
            session_id=str(session_id),
            run_id=str(curr_run_id),
            num_decisions=len(validated.approval_map),
        )

        # Install the retryable stream; the database CAS converges concurrent
        # setup on a single winning rotated stream. Duplicate submitters reuse
        # the installed attempt (marker match) and its Temporal update id.
        submission_key = self._approval_submission_key(
            workspace_id=self.workspace_id,
            session_id=session_id,
            run_id=curr_run_id,
            tool_call_ids=tuple(validated.approval_map.keys()),
        )
        attempt = await self._existing_approval_continuation_attempt(
            agent_session,
            submission_key=submission_key,
        )
        dedup_client: RedisClient | None = None
        claimed_submission = False
        if attempt is None:
            try:
                dedup_client = await get_redis_client()
                claimed_submission = await dedup_client.set_if_not_exists(
                    submission_key,
                    source,
                    expire_seconds=APPROVAL_CONTINUATION_DEDUP_TTL_SECONDS,
                )
            except Exception as exc:
                dedup_client = None
                logger.warning(
                    "Approval continuation dedup unavailable; proceeding best-effort",
                    session_id=str(session_id),
                    run_id=str(curr_run_id),
                    source=source,
                    error=str(exc),
                )

            if dedup_client is not None and not claimed_submission:
                logger.info(
                    "Skipping concurrent approval continuation submission",
                    session_id=str(session_id),
                    run_id=str(curr_run_id),
                    source=source,
                    submission_key=submission_key,
                )
                return None

            try:
                attempt = await self._prepare_approval_continuation_attempt(
                    agent_session=agent_session,
                    curr_run_id=curr_run_id,
                    submission_key=submission_key,
                )
            except Exception:
                if dedup_client is not None and claimed_submission:
                    with contextlib.suppress(Exception):
                        await dedup_client.delete(submission_key)
                raise

        try:
            did_resume = await self._submit_approval_update(
                agent_session=agent_session,
                curr_run_id=curr_run_id,
                attempt=attempt,
                validated=validated,
                handle=handle,
            )
        except BaseException as exc:
            is_ambiguous = isinstance(
                exc, (WorkflowUpdateRPCTimeoutOrCancelledError, RPCError)
            )
            if (
                dedup_client is not None
                and claimed_submission
                and isinstance(exc, Exception)
                and not is_ambiguous
            ):
                with contextlib.suppress(Exception):
                    await dedup_client.delete(submission_key)
            raise

        if not did_resume:
            await self._apply_submitted_approval_decisions(
                session_id=session_id,
                approval_map=validated.approval_map,
                decision_metadata=validated.decision_metadata,
            )
            await self._emit_approval_idle_segment(
                session_id=session_id,
                stream_id=attempt.stream_id,
                tool_call_ids=list(validated.approval_map),
            )

        logger.info(
            "Approval decisions submitted successfully",
            workflow_id=str(workflow_id),
            session_id=str(session_id),
            new_stream_id=str(attempt.stream_id),
            resumed=did_resume,
        )

        return ChatResponse(
            stream_url=f"/api/agent/sessions/{session_id}/stream",
            chat_id=session_id,
            active_stream_id=attempt.stream_id,
            curr_run_id=curr_run_id,
        )

    async def request_cancel(
        self,
        session_id: uuid.UUID,
        *,
        reason: Literal["user_cancel"] = "user_cancel",
    ) -> AgentSessionCancelResponse:
        """Request graceful cancellation for the active agent workflow turn.

        Raises:
            TracecatNotFoundError: If no session exists with this ID.
            TracecatConflictError: If the session has no active (RUNNING) turn.
        """
        from tracecat_ee.agent.types import AgentWorkflowID
        from tracecat_ee.agent.workflows.durable import (
            DurableAgentWorkflow,
            WorkflowCancelRequest,
        )

        agent_session = await self.get_session(session_id)
        if agent_session is None:
            raise TracecatNotFoundError(f"Session with ID {session_id} not found")

        if (
            AgentSessionEntity(agent_session.entity_type)
            is AgentSessionEntity.WORKSPACE_CHAT
        ):
            await self.require_entitlement(Entitlement.WORKSPACE_CHAT)

        lifecycle, curr_run_id = await self.get_turn_lifecycle(agent_session)
        if lifecycle is not TurnLifecycle.RUNNING or curr_run_id is None:
            raise TracecatConflictError(
                "Agent session does not have an active turn",
                detail={"lifecycle": lifecycle.value},
            )

        client = await get_temporal_client()
        workflow_id = AgentWorkflowID(curr_run_id)
        handle = client.get_workflow_handle_for(
            DurableAgentWorkflow.run,
            str(workflow_id),
        )

        logger.info(
            "Requesting agent turn cancellation",
            workflow_id=str(workflow_id),
            session_id=str(session_id),
            run_id=str(curr_run_id),
            reason=reason,
        )

        # Out-of-band fast path: the executor activity polls this signal and
        # interrupts the live runtime directly. Temporal activity cancellation
        # only reaches a running activity via throttled heartbeat RPCs, so a
        # turn can otherwise finish before the cancel is ever delivered. Write
        # it before the workflow update to minimize stop latency; best-effort
        # because the update-driven path still cancels (slower) without it.
        try:
            await signal_turn_cancel(str(curr_run_id), reason=reason)
        except Exception as e:
            logger.warning(
                "Failed to write turn cancel signal",
                session_id=str(session_id),
                run_id=str(curr_run_id),
                error=str(e),
            )

        await handle.execute_update(
            DurableAgentWorkflow.request_cancel,
            WorkflowCancelRequest(reason=reason),
        )

        return AgentSessionCancelResponse(
            session_id=session_id,
            run_id=curr_run_id,
            reason=reason,
        )

    @contextlib.asynccontextmanager
    async def _build_agent_config(
        self, agent_session: AgentSession
    ) -> AsyncIterator[AgentConfig]:
        """Build agent configuration for a session based on its entity type.

        Args:
            agent_session: The session to build config for.

        Yields:
            AgentConfig: The configured agent config.

        Raises:
            ValueError: If the session entity type is unsupported.
            TracecatNotFoundError: If required resources are not found.
        """
        agent_svc = AgentManagementService(self.session, self.role)

        if agent_session.entity_type is None:
            # No entity type - use the org's default model
            async with agent_svc.with_model_config() as model_config:
                yield AgentConfig(
                    instructions="",
                    model_name=model_config.name,
                    model_provider=model_config.provider,
                    catalog_id=model_config.catalog_id,
                    actions=agent_session.tools,
                )
            return

        session_entity = AgentSessionEntity(agent_session.entity_type)

        if session_entity is AgentSessionEntity.CASE:
            entity_instructions = await self._entity_to_prompt(agent_session)
            if agent_session.agent_preset_id:
                async with agent_svc.with_preset_config(
                    preset_id=agent_session.agent_preset_id,
                    preset_version_id=agent_session.agent_preset_version_id,
                ) as preset_config:
                    combined_instructions = (
                        f"{preset_config.instructions}\n\n{entity_instructions}"
                        if preset_config.instructions
                        else entity_instructions
                    )
                    config = replace(preset_config, instructions=combined_instructions)
                    yield config
            else:
                # Case chat without preset uses the org's default model
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions=entity_instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        catalog_id=model_config.catalog_id,
                        actions=agent_session.tools,
                    )
        elif session_entity is AgentSessionEntity.AGENT_PRESET:
            async with agent_svc.with_preset_config(
                preset_id=agent_session.entity_id,
                preset_version_id=agent_session.agent_preset_version_id,
            ) as preset_config:
                yield preset_config
        elif session_entity is AgentSessionEntity.EXTERNAL_CHANNEL:
            # External channels always execute against the linked preset.
            preset_id = agent_session.agent_preset_id or agent_session.entity_id
            async with agent_svc.with_preset_config(
                preset_id=preset_id,
                preset_version_id=agent_session.agent_preset_version_id,
            ) as preset_config:
                yield preset_config
        elif session_entity is AgentSessionEntity.AGENT_PRESET_BUILDER:
            if agent_session.entity_id is None:
                raise ValueError("Agent preset builder requires entity_id")
            instructions = await self._entity_to_prompt(agent_session)
            try:
                # Tools are resolved via MCP path in the durable workflow
                # (internal tools + bundled registry actions)
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions=instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        catalog_id=model_config.catalog_id,
                        actions=None,
                    )
            except TracecatNotFoundError as exc:
                raise ValueError(
                    "Agent preset builder requires a default AI model with valid provider credentials. "
                    "Configure credentials in Workspace settings before chatting."
                ) from exc
        elif session_entity is AgentSessionEntity.WORKSPACE_CHAT:
            # Copilot uses org-level credentials, not workspace credentials
            entity_instructions = await self._entity_to_prompt(agent_session)
            # Always-on platform skills (e.g. workflow management) staged for
            # every entitled workspace-chat session, with or without a preset.
            builtin_skills = await self._resolve_builtin_workspace_chat_skills()
            if agent_session.agent_preset_id:
                async with agent_svc.with_preset_config(
                    preset_id=agent_session.agent_preset_id,
                    preset_version_id=agent_session.agent_preset_version_id,
                ) as preset_config:
                    combined_instructions = (
                        f"{preset_config.instructions}\n\n{entity_instructions}"
                        if preset_config.instructions
                        else entity_instructions
                    )
                    # A preset carries its own actions/namespaces, so it must pass
                    # the same user-scope gate as the no-preset path -- otherwise
                    # attaching a preset (only `agent:execute` is needed) would let
                    # the agent run actions the user lacks scopes for. (`namespaces`
                    # only narrows this set at tool-build time, never widens it, so
                    # filtering `actions` is sufficient.)
                    scoped_actions = (
                        filter_workspace_chat_tools_for_scopes(
                            preset_config.actions, role=self.role
                        )
                        if preset_config.actions is not None
                        else None
                    )
                    config = replace(
                        preset_config,
                        instructions=combined_instructions,
                        actions=scoped_actions,
                        builtin_skills=builtin_skills,
                    )
                    yield config
            else:
                # Copilot without preset uses org-level credentials (default).
                # Always-on defaults merge with session extras at runtime, and
                # any attached MCP integrations resolve into mcp_servers.
                async with agent_svc.with_model_config() as model_config:
                    actions = await self._resolve_workspace_chat_actions(agent_session)
                    mcp_servers = await self._resolve_session_mcp_servers(
                        agent_session, agent_svc
                    )
                    yield AgentConfig(
                        instructions=entity_instructions,
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        catalog_id=model_config.catalog_id,
                        actions=actions,
                        mcp_servers=mcp_servers,
                        builtin_skills=builtin_skills,
                    )
        elif session_entity in (
            AgentSessionEntity.WORKFLOW,
            AgentSessionEntity.APPROVAL,
        ):
            # Check if this is a forked session (has parent_session_id)
            if agent_session.parent_session_id is not None:
                # Forked sessions use the parent's preset but with no tools.
                # Prepend context - agent should not mention this is a forked session.
                fork_context = (
                    "You do not have access to any tools in this conversation. "
                    "If the user asks you to perform actions, politely decline and "
                    "suggest they start a new workflow if they need to take action.\n\n"
                )
                # Get parent session to check for preset
                parent_session = await self.get_session(agent_session.parent_session_id)
                if parent_session and parent_session.agent_preset_id:
                    # Use parent's preset with forked context prepended
                    async with agent_svc.with_preset_config(
                        preset_id=parent_session.agent_preset_id,
                        preset_version_id=parent_session.agent_preset_version_id,
                    ) as preset_config:
                        combined_instructions = (
                            f"{fork_context}{preset_config.instructions}"
                            if preset_config.instructions
                            else fork_context.strip()
                        )
                        yield AgentConfig(
                            instructions=combined_instructions,
                            model_name=preset_config.model_name,
                            model_provider=preset_config.model_provider,
                            catalog_id=preset_config.catalog_id,
                            actions=[],  # No tools for forked sessions
                            enable_thinking=preset_config.enable_thinking,
                        )
                else:
                    # No preset - use org default model with fork context
                    async with agent_svc.with_model_config() as model_config:
                        yield AgentConfig(
                            instructions=fork_context.strip(),
                            model_name=model_config.name,
                            model_provider=model_config.provider,
                            catalog_id=model_config.catalog_id,
                            actions=[],  # No tools for forked sessions
                        )
            elif agent_session.agent_preset_id:
                # Workflow sessions with preset use the preset config
                async with agent_svc.with_preset_config(
                    preset_id=agent_session.agent_preset_id,
                    preset_version_id=agent_session.agent_preset_version_id,
                ) as preset_config:
                    yield preset_config
            else:
                # Workflow without preset uses the org's default model
                async with agent_svc.with_model_config() as model_config:
                    yield AgentConfig(
                        instructions="",
                        model_name=model_config.name,
                        model_provider=model_config.provider,
                        catalog_id=model_config.catalog_id,
                        actions=agent_session.tools,
                    )
        else:
            raise ValueError(
                f"Unsupported session entity type: {agent_session.entity_type}. "
                f"Expected one of: {list(AgentSessionEntity)}"
            )

    async def _entity_to_prompt(self, agent_session: AgentSession) -> str:
        """Get the prompt for a given entity type."""
        entity_type = agent_session.entity_type
        entity_id = agent_session.entity_id

        if entity_type == AgentSessionEntity.CASE:
            cases_service = CasesService(self.session, self.role)
            case = await cases_service.get_case(entity_id)
            if not case:
                raise TracecatNotFoundError(f"Case with ID {entity_id} not found")
            return CaseCopilotPrompts(case=case).instructions
        if entity_type == AgentSessionEntity.AGENT_PRESET_BUILDER:
            agent_preset_service = AgentPresetService(self.session, self.role)
            if not (preset := await agent_preset_service.get_preset(entity_id)):
                raise TracecatNotFoundError(
                    f"Agent preset with ID '{entity_id}' not found"
                )
            prompt = AgentPresetBuilderPrompt(preset=preset)
            return prompt.instructions
        if entity_type == AgentSessionEntity.WORKSPACE_CHAT:
            return WorkspaceCopilotPrompts().instructions
        else:
            raise ValueError(
                f"Unsupported session entity type: {entity_type}. "
                f"Expected one of: {list(AgentSessionEntity)}"
            )

    # =========================================================================
    # Message Retrieval
    # =========================================================================

    async def _visible_history_entries(
        self,
        *,
        session_ids: Sequence[uuid.UUID],
        current_run_id: uuid.UUID | None,
        approval_tool_call_ids: set[str],
        include_active: bool,
    ) -> list[AgentSessionHistory]:
        """Query the history rows visible to this message read."""
        approval_boundary: int | None = None
        if not include_active and current_run_id is not None and approval_tool_call_ids:
            boundary_stmt = (
                select(
                    AgentSessionHistory.surrogate_id,
                    AgentSessionHistory.content,
                )
                .where(
                    AgentSessionHistory.session_id.in_(session_ids),
                    AgentSessionHistory.curr_run_id == current_run_id,
                )
                .order_by(AgentSessionHistory.surrogate_id.desc())
            )
            result = await self.session.execute(boundary_stmt)
            for surrogate_id, content in result.tuples():
                inner_message = (content.get("message") or content) if content else {}
                if any(
                    tool_use.get("id") in approval_tool_call_ids
                    for tool_use in self._extract_tool_uses_from_message(inner_message)
                ):
                    approval_boundary = surrogate_id
                    break

        stmt = (
            select(AgentSessionHistory)
            .where(AgentSessionHistory.session_id.in_(session_ids))
            .order_by(AgentSessionHistory.surrogate_id)
        )
        if not include_active and current_run_id is not None:
            visible = or_(
                AgentSessionHistory.curr_run_id.is_(None),
                AgentSessionHistory.curr_run_id != current_run_id,
            )
            if approval_boundary is not None:
                visible = or_(
                    visible,
                    and_(
                        AgentSessionHistory.curr_run_id == current_run_id,
                        AgentSessionHistory.surrogate_id <= approval_boundary,
                    ),
                )
            stmt = stmt.where(visible)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_messages(
        self,
        session_id: uuid.UUID,
        *,
        kinds: Sequence[MessageKind] | None = None,
        include_active: bool = False,
    ) -> list[ChatMessage]:
        """Retrieve session messages, optionally filtered by message kind.

        For forked sessions, includes parent session messages first.
        Checks the new AgentSessionHistory table first, then falls back to
        the legacy ChatMessage table for backward compatibility.

        Args:
            session_id: The session UUID (could be AgentSession.id or Chat.id).
            kinds: Optional list of message kinds to filter by.
            include_active: When True, do not hide the active turn's rows. The
                mid-turn filter exists for live UI reads (the assistant streams
                from Redis). Terminal loads that build ``AgentOutput`` must set
                this True: they run before ``finalize_turn`` clears
                ``curr_run_id``, so the default filter would otherwise omit the
                just-completed turn from the returned ``message_history``.

        Returns:
            List of ChatMessage objects (parent messages + current if forked).
        """
        agent_session = await self.get_session(session_id)

        # If no history in new table, fall back to legacy ChatMessage table
        if not agent_session:
            chat_service = ChatService(self.session, self.role)
            return await chat_service.list_legacy_messages(session_id, kinds=kinds)

        session_ids = [session_id]
        if agent_session and agent_session.parent_session_id:
            session_ids.insert(0, agent_session.parent_session_id)

        approval_result = await self.session.execute(
            select(Approval).where(Approval.session_id.in_(session_ids))
        )
        approval_by_tool_id = {
            approval.tool_call_id: approval
            for approval in approval_result.scalars().all()
        }
        entries = await self._visible_history_entries(
            session_ids=session_ids,
            current_run_id=agent_session.curr_run_id,
            approval_tool_call_ids=set(approval_by_tool_id),
            include_active=include_active,
        )

        return self._render_history_entries(
            entries,
            approvals_by_tool_id=approval_by_tool_id,
            kinds=kinds,
        )

    def _render_history_entries(
        self,
        entries: Sequence[AgentSessionHistory],
        *,
        approvals_by_tool_id: dict[str, Approval],
        kinds: Sequence[MessageKind] | None,
    ) -> list[ChatMessage]:
        """Render visible history rows into the interleaved chat timeline."""
        messages: list[ChatMessage] = []
        internal_uuids: set[str] = set()

        for entry in entries:
            content = entry.content
            if not content:
                continue

            if entry.kind == MessageKind.INTERNAL.value:
                if line_uuid := session_line_uuid(content):
                    internal_uuids.add(line_uuid)
                continue

            if is_continuation_control_artifact(content, internal_uuids):
                if line_uuid := session_line_uuid(content):
                    internal_uuids.add(line_uuid)
                continue

            if entry.kind == MessageKind.COMPACTION.value:
                kind = MessageKind.COMPACTION
                if kinds and kind not in kinds:
                    continue

                compaction_data: dict[str, Any] = {"phase": "completed"}
                compact_metadata = content.get("compactMetadata")
                if isinstance(compact_metadata, dict):
                    pre_tokens = compact_metadata.get("preTokens")
                    if isinstance(pre_tokens, int):
                        compaction_data["pre_tokens"] = pre_tokens

                messages.append(
                    ChatMessage(
                        id=str(entry.id),
                        kind=kind,
                        compaction=compaction_data,
                    )
                )
                continue

            if entry.kind == MessageKind.CANCELLED.value:
                kind = MessageKind.CANCELLED
                if kinds and kind not in kinds:
                    continue

                reason = content.get("reason")
                raw_tool_call_ids = content.get("tool_call_ids")
                tool_call_ids = [
                    item
                    for item in (
                        raw_tool_call_ids if isinstance(raw_tool_call_ids, list) else []
                    )
                    if isinstance(item, str)
                ]
                cancelled_payload: dict[str, Any] = {
                    "reason": reason if isinstance(reason, str) else None,
                }
                if tool_call_ids:
                    cancelled_payload["tool_call_ids"] = tool_call_ids
                messages.append(
                    ChatMessage(
                        id=str(entry.id),
                        kind=kind,
                        cancelled=cancelled_payload,
                    )
                )
                continue

            msg_type = content.get("type")
            if msg_type not in ("user", "assistant"):
                continue
            if kinds and MessageKind.CHAT_MESSAGE not in kinds:
                continue

            inner_message = content.get("message") or content
            sanitized_message = sanitize_message_tool_inputs(inner_message)
            message = ClaudeSDKMessageTA.validate_python(sanitized_message)
            messages.append(ChatMessage(id=str(entry.id), message=message))

            if msg_type != "assistant":
                continue
            for tool_use in self._extract_tool_uses_from_message(sanitized_message):
                tool_use_id = tool_use.get("id")
                if not tool_use_id or not (
                    approval := approvals_by_tool_id.get(tool_use_id)
                ):
                    continue

                approval_read = ApprovalRead.model_validate(approval)
                messages.append(
                    ChatMessage(
                        id=str(approval.id),
                        kind=MessageKind.APPROVAL_REQUEST,
                        approval=approval_read,
                    )
                )
                if approval.status != ApprovalStatus.PENDING:
                    messages.append(
                        ChatMessage(
                            id=f"{approval.id}-decision",
                            kind=MessageKind.APPROVAL_DECISION,
                            approval=approval_read,
                        )
                    )

        return messages

    @staticmethod
    def _extract_tool_uses_from_message(
        message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract tool_use blocks from a Claude SDK message."""
        content = message.get("content", [])
        if isinstance(content, str):
            return []
        return [
            block
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]

    @classmethod
    def _assistant_row_tool_call_ids(cls, entry: AgentSessionHistory) -> set[str]:
        """Return tool_use IDs on an assistant history row, else empty."""
        if entry.content.get("type") != "assistant":
            return set()
        message = entry.content.get("message", {})
        if not isinstance(message, dict):
            return set()
        return {
            tool_use_id
            for tool_use in cls._extract_tool_uses_from_message(message)
            if isinstance(tool_use_id := tool_use.get("id"), str)
        }

    # =========================================================================
    # Approval Flow: Replace Interrupt Entries
    # =========================================================================

    async def replace_interrupt_with_tool_results(
        self,
        session_id: uuid.UUID,
        tool_results: Sequence[ToolExecutionResult],
    ) -> None:
        """Replace interrupted approval artifacts with a real tool_result entry.

        After approval execution, the session history contains SDK-generated
        interrupt entries (error tool_result, interrupt text, synthetic message).
        This method:
        1. Finds the assistant message with tool_use blocks
        2. Deletes the interrupt entries
        3. Inserts the approved/denied tool_result as the next user entry

        Claude Code must see tool_result immediately after the assistant tool_use
        when it loads the resumed session. If we stream tool_result after the CLI
        starts, the CLI may first append a synthetic no-op assistant entry.

        Args:
            session_id: The session UUID.
            tool_results: Tool execution results (both approved and denied).
        """
        if not tool_results:
            return

        session = await self.get_session(session_id)
        if not session:
            raise TracecatNotFoundError(f"Session {session_id} not found")
        if not session.sdk_session_id:
            raise ValueError(f"Session {session_id} has no sdk_session_id")

        tool_call_ids = {tr.tool_call_id for tr in tool_results}

        # Find the assistant rows containing these tool_uses so we only delete
        # interrupt artifacts that follow the pending tool calls. Claude Code
        # writes parallel tool calls as one assistant JSONL row per tool_use
        # block, with every row of the batch sharing the API message id — so
        # the pending IDs are unioned across rows of one message only; calls
        # from different assistant turns must never be reconciled together.
        # The tool_result entry must be anchored after the LAST such row,
        # which is the first one encountered walking in reverse.
        #
        # `message.id` is best-effort: some SDK rows omit it. When it's
        # present on the anchor row, union any earlier row sharing that id
        # (order-independent). When it's absent, fall back to unioning only
        # immediately adjacent assistant rows with no id of their own — a
        # gap (any non-assistant row, or one that does carry an id) means a
        # different assistant turn has begun.
        history = await self.get_session_history(session_id)
        assistant_entry: AgentSessionHistory | None = None
        anchor_index: int | None = None
        anchor_message_id: str | None = None
        covered_tool_call_ids: set[str] = set()

        for index in range(len(history) - 1, -1, -1):
            entry = history[index]
            row_tool_call_ids = self._assistant_row_tool_call_ids(entry)
            matched = tool_call_ids & row_tool_call_ids
            if not matched:
                if assistant_entry is not None and anchor_message_id is None:
                    # No message id to key off of: an unrelated row breaks
                    # the contiguous run of the current batch.
                    break
                continue

            message = entry.content.get("message", {})
            message_id = message.get("id") if isinstance(message, dict) else None
            message_id = message_id if isinstance(message_id, str) else None

            if assistant_entry is None:
                assistant_entry = entry
                anchor_message_id = message_id
            elif anchor_message_id is not None:
                if message_id != anchor_message_id:
                    continue
            elif (
                anchor_index is None
                or index != anchor_index - 1
                or message_id is not None
            ):
                # Not contiguous with the last unioned row, or this row has
                # its own id - it belongs to a different batch/turn.
                break

            anchor_index = index
            covered_tool_call_ids |= matched
            if tool_call_ids.issubset(covered_tool_call_ids):
                break

        if assistant_entry is None or not tool_call_ids.issubset(covered_tool_call_ids):
            logger.warning(
                "Could not find assistant message(s) with tool_use for continuation",
                session_id=session_id,
                tool_call_ids=tool_call_ids,
                covered_tool_call_ids=covered_tool_call_ids,
            )
            return

        assistant_content = assistant_entry.content
        assistant_uuid = assistant_content.get("uuid")
        if not isinstance(assistant_uuid, str):
            logger.warning(
                "Assistant tool_use entry is missing uuid for continuation",
                session_id=session_id,
                tool_call_ids=tool_call_ids,
            )
            return

        # Delete interrupt entries that follow the assistant message
        await self._delete_interrupt_entries_for_tool_calls(
            session_id, assistant_entry.surrogate_id, tool_call_ids
        )

        # Avoid duplicate tool_result rows if the activity is retried after the
        # replacement has already been committed.
        if await self._has_tool_result_entry_after(
            session_id, assistant_entry.surrogate_id, tool_call_ids
        ):
            await self.session.commit()
            logger.info(
                "Tool_result entry already exists for approval continuation",
                session_id=session_id,
                tool_call_ids=list(tool_call_ids),
            )
            return

        entry_content: dict[str, Any] = {
            "uuid": str(uuid.uuid4()),
            "parentUuid": assistant_uuid,
            "sessionId": session.sdk_session_id,
            "type": "user",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "cwd": assistant_content.get("cwd") or "/home/agent",
            "version": assistant_content.get("version") or "2.1.85",
            "userType": assistant_content.get("userType") or "external",
            "gitBranch": assistant_content.get("gitBranch") or "",
            "entrypoint": assistant_content.get("entrypoint") or "sdk-py",
            "isSidechain": False,
            "permissionMode": "default",
            "promptId": str(uuid.uuid4()),
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": self._serialize_tool_result(result.result),
                        "is_error": result.is_error,
                    }
                    for result in tool_results
                ],
            },
        }

        # Tag the inserted tool_result with the active run id so the mid-turn
        # filter hides it alongside its (also active-run-tagged) assistant
        # tool_use row. A NULL tag would leave this row visible while the
        # matching tool_use stays hidden, rendering a dangling/duplicate tool
        # result on a mid-turn DB reload until terminal cleanup. Terminal nulls
        # curr_run_id, at which point both rows become visible together.
        self.session.add(
            AgentSessionHistory(
                session_id=session_id,
                workspace_id=self.workspace_id,
                content=entry_content,
                kind=MessageKind.CHAT_MESSAGE.value,
                curr_run_id=session.curr_run_id,
            )
        )
        await self.session.commit()

        logger.info(
            "Replaced interrupt entries with tool_result",
            session_id=session_id,
            tool_call_ids=list(tool_call_ids),
            parent_uuid=assistant_uuid,
        )

    async def _has_tool_result_entry_after(
        self,
        session_id: uuid.UUID,
        assistant_surrogate_id: int,
        tool_call_ids: set[str],
    ) -> bool:
        """Return True when all approval tool calls already have real results.

        This is the idempotency guard for approval reconciliation retries. SDK
        approval interrupts also write error `tool_result` blocks, so those
        placeholder rows must be ignored here; otherwise a retry could mistake
        stale interrupt state for the approved/denied tool execution result.
        """
        if not tool_call_ids:
            return False

        # Only user messages with list content can contain Anthropic
        # `tool_result` blocks. Treat other content shapes as empty so malformed
        # or text-only rows cannot satisfy the idempotency check.
        message_content = AgentSessionHistory.content["message"]["content"]
        message_content_array = case(
            (func.jsonb_typeof(message_content) == "array", message_content),
            else_=literal([], type_=JSONB),
        )
        content_blocks = (
            func.jsonb_array_elements(message_content_array)
            .table_valued(column("value", JSONB))
            .alias("content_block")
        )
        block = content_blocks.c.value

        # The SDK writes approval-interrupt placeholders as error tool_results
        # for the same tool_use IDs. Those rows should be deleted/replaced, not
        # treated as the real reconciled tool result.
        block_text = func.lower(func.btrim(block["content"].astext))
        is_approval_interrupt = and_(
            block["is_error"].astext == "true",
            or_(
                block_text == APPROVAL_INTERRUPT_CONTENT_EXACT,
                *(
                    block_text.contains(marker)
                    for marker in APPROVAL_INTERRUPT_CONTENT_MARKERS
                ),
            ),
        )
        # A multi-tool approval continuation is only reconciled once every
        # pending tool_use has a non-placeholder result after the assistant row.
        matching_tool_result_count = (
            select(func.count(func.distinct(block["tool_use_id"].astext)))
            .select_from(content_blocks)
            .where(
                block["type"].astext == "tool_result",
                block["tool_use_id"].astext.in_(tool_call_ids),
                ~is_approval_interrupt,
            )
            .correlate(AgentSessionHistory)
            .scalar_subquery()
        )

        stmt = (
            select(literal(True))
            .where(
                AgentSessionHistory.workspace_id == self.workspace_id,
                AgentSessionHistory.session_id == session_id,
                AgentSessionHistory.surrogate_id > assistant_surrogate_id,
                AgentSessionHistory.content["type"].astext == "user",
                matching_tool_result_count == len(tool_call_ids),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is True

    async def _delete_interrupt_entries_for_tool_calls(
        self,
        session_id: uuid.UUID,
        assistant_surrogate_id: int,
        tool_call_ids: set[str],
    ) -> None:
        """Delete interrupt entries that follow the assistant tool_use message.

        Entries to delete:
        1. Error tool_result: type="user", content has tool_result with is_error=true
        2. Interrupt text: type="user", content contains "[Request interrupted"
        3. Synthetic: type="assistant", message.model="<synthetic>"

        Args:
            session_id: The session UUID.
            assistant_surrogate_id: The surrogate_id of the assistant message with tool_use.
            tool_call_ids: Set of tool_call_ids we're replacing.
        """
        # Get entries after the assistant message
        stmt = (
            select(AgentSessionHistory)
            .where(
                AgentSessionHistory.session_id == session_id,
                AgentSessionHistory.surrogate_id > assistant_surrogate_id,
            )
            .order_by(AgentSessionHistory.surrogate_id)
        )
        result = await self.session.execute(stmt)
        entries = list(result.scalars().all())

        entries_to_delete: list[AgentSessionHistory] = []

        for entry in entries:
            content = entry.content
            if not content:
                continue

            msg_type = content.get("type")
            message = content.get("message", {})
            msg_content = message.get("content", [])

            # Check for error tool_result or interrupt text in user messages
            if msg_type == "user" and isinstance(msg_content, list):
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue
                    # Check for error tool_result matching our tool_call_ids
                    if is_approval_interrupt_tool_result(block, tool_call_ids):
                        entries_to_delete.append(entry)
                        break
                    # Check for interrupt text
                    if block.get(
                        "type"
                    ) == "text" and "[Request interrupted" in block.get("text", ""):
                        entries_to_delete.append(entry)
                        break

            # Check for synthetic assistant message
            elif msg_type == "assistant" and message.get("model") == "<synthetic>":
                entries_to_delete.append(entry)

        # Delete the identified entries
        if entries_to_delete:
            ids_to_delete = [e.id for e in entries_to_delete]
            delete_stmt = delete(AgentSessionHistory).where(
                AgentSessionHistory.id.in_(ids_to_delete)
            )
            await self.session.execute(delete_stmt)

            logger.info(
                "Deleted interrupt entries",
                session_id=session_id,
                deleted_count=len(entries_to_delete),
                deleted_ids=[str(id) for id in ids_to_delete],
            )

    @staticmethod
    def _serialize_tool_result(result: Any) -> str:
        """Serialize a tool result to Claude's string tool_result content."""
        if isinstance(result, str):
            return result
        try:
            return orjson.dumps(result).decode("utf-8")
        except (TypeError, ValueError):
            return str(result)

    # =========================================================================
    # Session Forking (for post-decision agent interactions)
    # =========================================================================

    async def fork_session(
        self,
        parent_session_id: uuid.UUID,
        *,
        entity_type: AgentSessionEntity | None = None,
    ) -> AgentSession:
        """Create a forked session from a parent session.

        Forked sessions allow users to continue interacting with an agent
        after making approval decisions, to ask for context or clarification.

        Args:
            parent_session_id: The ID of the session to fork.
            entity_type: Override entity type for the forked session. If None,
                inherits from parent. Use APPROVAL for inbox forks to hide
                from main chat list.

        Returns:
            The newly created forked AgentSession.

        Raises:
            TracecatNotFoundError: If the parent session is not found.
        """
        parent = await self.get_session(parent_session_id)
        if parent is None:
            raise TracecatNotFoundError(
                f"Parent session with ID {parent_session_id} not found"
            )

        # Forked sessions are read-only "reviewer" sessions.
        forked_session = AgentSession(
            workspace_id=self.workspace_id,
            # Metadata - inherit from parent, except entity_type if overridden
            title=f"{parent.title} (continued)",
            created_by=self.role.user_id,
            entity_type=entity_type.value if entity_type else parent.entity_type,
            entity_id=parent.entity_id,
            channel_context=parent.channel_context,
            tools=[],
            agent_preset_id=None,
            work_dir_snapshot=copy.deepcopy(parent.work_dir_snapshot),
            # Harness - inherit from parent
            harness_type=parent.harness_type,
            # Fork reference
            parent_session_id=parent_session_id,
        )
        self.session.add(forked_session)
        await self.session.commit()
        await self.session.refresh(forked_session)

        logger.info(
            "Created forked session",
            forked_session_id=forked_session.id,
            parent_session_id=parent_session_id,
        )

        return forked_session
