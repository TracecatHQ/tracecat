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
            "You may not execute external tools or run the agent yourself. "
            "You may only refer to the existing agent configuration and suggest edits."
        )
        context = (
            "Agent Preset Context:\n"
            f"- Name: {self.preset.name}\n"
            f"- Slug: {self.preset.slug}\n"
            f"- Description: {description}\n\n"
            "Current System Prompt:\n"
            f"{system_prompt}"
        )
        return "\n\n".join([header, guidelines, constraints, context])
