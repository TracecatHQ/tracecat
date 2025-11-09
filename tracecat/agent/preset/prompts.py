"""Prompt helpers for agent preset-related chats."""

from __future__ import annotations

from dataclasses import dataclass

from tracecat.db.models import AgentPreset


@dataclass(slots=True)
class AgentPresetBuilderPrompt:
    """Builds instructions for the agent preset builder assistant."""

    preset: AgentPreset

    @property
    def instructions(self) -> str:
        description = self.preset.description or "(no description provided)"
        system_prompt = self.preset.instructions or "(currently empty)"
        header = (
            "You are Tracecat's agent preset builder assistant. Your job is to help "
            "workspace users refine the system prompt and tool configuration for the selected agent preset."
        )
        guidelines = (
            "Keep the conversation focused on improving the agent's instructions, allowed toolset, and manual approval rules. "
            "Provide concrete suggestions, alternative phrasings, clarifying questions, and rationale. "
            "When adjustments are needed, call the provided tools to update instructions, actions, namespaces, or tool approvals directly."
        )
        constraints = (
            "Do not execute external tools or run the agent yourself—only use the service-layer tools described below. "
            "You may only inspect and edit the agent preset; never invoke customer workflows or actions directly."
        )
        tooling = (
            "You can call service-layer tools to inspect and update the preset. "
            "Use `get_agent_preset_summary()` to fetch the latest configuration. "
            "Use `update_agent_preset()` to apply changes—pass only the fields that should change, "
            "leaving all other parameters unspecified so they remain untouched. "
            "Important fields include `instructions` for the system prompt, `actions` for allowed tools, "
            "`namespaces` for dynamic discovery limits, and `tool_approvals` for manual approval requirements."
        )
        allowed_tools = ", ".join(self.preset.actions or []) or "(none selected)"
        namespace_limits = (
            ", ".join(self.preset.namespaces or []) or "All namespaces allowed"
        )
        approvals = self.preset.tool_approvals or {}
        if approvals:
            approval_lines = [
                f"  - {tool}: {'auto-run (no approval)' if allow else 'requires manual approval'}"
                for tool, allow in approvals.items()
            ]
            approval_summary = "\n".join(approval_lines)
        else:
            approval_summary = "  - (no manual approval rules configured)"
        context = (
            "Agent Preset Context:\n"
            f"- Name: {self.preset.name}\n"
            f"- Slug: {self.preset.slug}\n"
            f"- Description: {description}\n\n"
            "Current System Prompt:\n"
            f"{system_prompt}\n\n"
            "Tool Configuration:\n"
            f"- Allowed tools: {allowed_tools}\n"
            f"- Namespace limits: {namespace_limits}\n"
            "Manual approvals:\n"
            f"{approval_summary}"
        )
        return "\n\n".join([header, guidelines, constraints, tooling, context])
