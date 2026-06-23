"""Domain types for agent session management."""

from enum import StrEnum


class TurnLifecycle(StrEnum):
    """In-memory reconnect decision derived live from Temporal.

    Never persisted to the DB - Temporal owns turn lifecycle. Computed from
    ``describe_workflow(curr_run_id)`` on the (cold) reconnect path.

    - NONE: no current run; nothing to attach to (-> 204).
    - RUNNING: workflow live; join the Redis stream from the client cursor.
    - COMPLETED: turn done; canonical history is in the DB (-> 204).
    - FAILED: workflow failed/terminated (incl. failed-to-start); emit a
      terminal error frame + done so the client doesn't hang.
    """

    NONE = "none"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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
