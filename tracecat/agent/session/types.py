"""Domain types for agent session management."""

from enum import StrEnum
from typing import Literal


class AgentSessionEntity(StrEnum):
    """The type of entity associated with an agent session.

    Determines the context and behavior of the session:
    - CASE: Chat attached to a Case entity for investigation
    - AGENT_PRESET: Live chat testing a preset configuration
    - AGENT_PRESET_BUILDER: Builder chat for editing/configuring a preset
    - COPILOT: Workspace-level copilot assistant
    - WORKFLOW: Workflow-initiated agent run (from action)
    - APPROVAL: Inbox approval continuation (hidden from main chat list)
    - EXTERNAL_CHANNEL: External channel session (e.g. Slack thread)
    """

    CASE = "case"
    AGENT_PRESET = "agent_preset"
    AGENT_PRESET_BUILDER = "agent_preset_builder"
    COPILOT = "copilot"
    WORKFLOW = "workflow"
    APPROVAL = "approval"
    EXTERNAL_CHANNEL = "external_channel"


class AgentSessionStatus(StrEnum):
    """Lifecycle state for an agent session turn."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    STOPPED = "stopped"
    FAILED = "failed"


type AgentCancelReason = Literal["user_cancel", "worker_drain"]
type AgentWorkflowTurnStatus = Literal[
    "idle",
    "running",
    "waiting_for_approval",
    "stopped",
    "failed",
]
