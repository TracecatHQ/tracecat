"""Domain types for agent session management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple

from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.identifiers import UserID


def is_session_owner(role: Role, created_by: UserID | None) -> bool:
    """Return whether an actor owns a session with this creator.

    User-backed actors own the sessions they created. Service accounts own
    sessions without a user creator. Unbound internal services own every
    session so background execution is not blocked.
    """
    if role.type == "service_account":
        return created_by is None
    if role.user_id is not None:
        return created_by == role.user_id
    return True


def can_act_on_session(
    role: Role,
    entity_type: str,
    created_by: UserID | None,
    *,
    participated: bool = False,
) -> bool:
    """Return whether an actor may act on a session: message, steer, stop it.

    Workflow and channel-started sessions belong to the workspace, and a session
    the actor has sent a message in (``participated``) is theirs too, so any
    member with ``agent:execute`` may act on them. Every other session is its
    owner's.
    """
    if is_session_owner(role, created_by):
        return True
    shared = (
        participated
        or entity_type == AgentSessionEntity.WORKFLOW
        or is_ownerless_channel_session(entity_type, created_by)
    )
    return (
        shared and role.scopes is not None and has_scope(role.scopes, "agent:execute")
    )


def can_decide_on_session(
    role: Role,
    entity_type: str,
    created_by: UserID | None,
    *,
    participated: bool = False,
) -> bool:
    """Return whether an actor may decide a session's pending approvals.

    Anyone may decide on an ownerless channel session, as anyone in the channel can.
    """
    return can_act_on_session(
        role, entity_type, created_by, participated=participated
    ) or is_ownerless_channel_session(entity_type, created_by)


def is_ownerless_channel_session(entity_type: str, created_by: UserID | None) -> bool:
    """A session started from an external channel: no creator owns it."""
    return entity_type == AgentSessionEntity.EXTERNAL_CHANNEL and created_by is None


class TurnLifecycle(StrEnum):
    """In-memory reconnect decision derived live from Temporal.

    Never persisted to the DB - Temporal owns turn lifecycle. Computed from
    ``describe_workflow(curr_run_id)`` on the (cold) reconnect path.

    - UNAVAILABLE: the backend is not installed; the live outcome is unknown.
    - NONE: no current run; nothing to attach to (-> 204).
    - RUNNING: workflow live; join the Redis stream from the client cursor.
    - COMPLETED: turn done; canonical history is in the DB (-> 204).
    - FAILED: workflow failed/terminated (incl. failed-to-start); emit a
      terminal error frame + done so the client doesn't hang.
    - CANCELLED: workflow's own Temporal execution status is CANCELED
      (e.g. an operator called handle.cancel()/handle.terminate() directly).
      This is defense-in-depth only: a normal user-initiated cancel goes
      through the `request_cancel` workflow update, which lets the workflow
      return normally, so get_turn_lifecycle reports it as COMPLETED, not
      CANCELLED. The real "was this turn cancelled" signal for clients is
      the `data-cancelled` stream event emitted mid-turn, not this value.
    """

    UNAVAILABLE = "unavailable"
    NONE = "none"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TurnLifecycleResult(NamedTuple):
    """Result of resolving a turn's live lifecycle from Temporal.

    ``run_id`` is ``None`` exactly when ``lifecycle`` is ``TurnLifecycle.NONE``.
    """

    lifecycle: TurnLifecycle
    run_id: uuid.UUID | None


class AgentSessionEntity(StrEnum):
    """The type of entity associated with an agent session.

    Determines the context and behavior of the session:
    - CASE: Chat attached to a Case entity for investigation
    - AGENT_PRESET: Live chat testing a preset configuration
    - AGENT_PRESET_BUILDER: Builder chat for editing/configuring a preset
    - WORKSPACE_CHAT: Workspace-level chat assistant (wire value: copilot)
    - WORKFLOW: Workflow-initiated agent run (from action)
    - APPROVAL: Inbox approval continuation (hidden from main chat list)
    - EXTERNAL_CHANNEL: External channel session (e.g. Slack thread)
    """

    CASE = "case"
    AGENT_PRESET = "agent_preset"
    AGENT_PRESET_BUILDER = "agent_preset_builder"
    # Keep the wire/storage value as "copilot" for rollback compatibility while
    # exposing the product concept as WORKSPACE_CHAT in backend code.
    WORKSPACE_CHAT = "copilot"
    WORKFLOW = "workflow"
    APPROVAL = "approval"
    EXTERNAL_CHANNEL = "external_channel"


@dataclass(frozen=True, slots=True)
class PreparedAgentTurn:
    """Persisted turn state for a caller that owns Temporal dispatch."""

    prompt: str
    session_id: uuid.UUID
    run_id: uuid.UUID
    active_stream_id: uuid.UUID
    config: AgentConfig
    title: str
    entity_type: AgentSessionEntity
    entity_id: uuid.UUID
    tools: list[str] | None
    agent_preset_id: uuid.UUID | None
    agent_preset_version_id: uuid.UUID | None
    backend_id: str
    harness_type: str | None
