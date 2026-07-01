"""Domain types for agent session management."""

import uuid
from enum import StrEnum
from typing import NamedTuple


class TurnLifecycle(StrEnum):
    """In-memory reconnect decision derived live from Temporal.

    Never persisted to the DB - Temporal owns turn lifecycle. Computed from
    ``describe_workflow(curr_run_id)`` on the (cold) reconnect path.

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
