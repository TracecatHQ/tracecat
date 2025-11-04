"""Prompt helpers for agent profile-related chats."""

from __future__ import annotations

from dataclasses import dataclass

from tracecat.agent.profiles.schemas import AgentProfileRead


@dataclass(slots=True)
class AgentProfileBuilderPrompt:
    """Builds instructions for the agent profile builder assistant."""

    profile: AgentProfileRead

    @property
    def instructions(self) -> str:
        description = self.profile.description or "(no description provided)"
        system_prompt = self.profile.instructions or "(currently empty)"
        header = (
            "You are Tracecat's agent profile builder assistant. Your job is to help "
            "workspace users refine the system prompt and configuration for the selected agent profile."
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
            "Agent Profile Context:\n"
            f"- Name: {self.profile.name}\n"
            f"- Slug: {self.profile.slug}\n"
            f"- Description: {description}\n\n"
            "Current System Prompt:\n"
            f"{system_prompt}"
        )
        return "\n\n".join([header, guidelines, constraints, context])
