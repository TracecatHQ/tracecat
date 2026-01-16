"""Domain types for agent session management."""

from enum import StrEnum


class AgentSessionEntity(StrEnum):
    """The type of entity associated with an agent session.

    Determines the context and behavior of the session:
    - CASE: Chat attached to a Case entity for investigation
    - AGENT_PRESET: Live chat testing a preset configuration
    - AGENT_PRESET_BUILDER: Builder chat for editing/configuring a preset
    - COPILOT: Workspace-level copilot assistant
    - WORKFLOW: Workflow-initiated agent run (from action)
    - APPROVAL: Inbox approval continuation (hidden from main chat list)
    """

    CASE = "case"
    AGENT_PRESET = "agent_preset"
    AGENT_PRESET_BUILDER = "agent_preset_builder"
    COPILOT = "copilot"
    WORKFLOW = "workflow"
    APPROVAL = "approval"
