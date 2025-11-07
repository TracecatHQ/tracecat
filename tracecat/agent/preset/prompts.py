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
            "workspace users refine the system prompt and configuration for the selected agent preset."
        )
        guidelines = (
            "Keep the conversation focused on improving the agent's instructions. "
            "Provide concrete suggestions, alternative phrasings, clarifying questions, and rationale. "
            "When the user accepts changes, respond with clear guidance describing what should be updated in the form."
        )
        constraints = (
            "Do not execute external tools or run the agent yourself—only use the service-layer tools described below. "
            "You may only refer to the existing agent configuration and suggest edits."
        )
        tooling = (
            "You can call service-layer tools to inspect and update the preset. "
            "Use `get_agent_preset_summary()` to fetch the latest configuration. "
            "Use `update_agent_preset()` to apply changes—pass only the fields that should change, "
            "leaving all other parameters unspecified so they remain untouched."
        )
        context = (
            "Agent Preset Context:\n"
            f"- Name: {self.preset.name}\n"
            f"- Slug: {self.preset.slug}\n"
            f"- Description: {description}\n\n"
            "Current System Prompt:\n"
            f"{system_prompt}"
        )
        return "\n\n".join([header, guidelines, constraints, tooling, context])
