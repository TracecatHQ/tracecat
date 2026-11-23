"""Prompt text that teaches agents the mention format."""

from tracecat.agent.session.types import AgentSessionEntity

ARTIFACT_MENTION_PROMPT = (
    "<ArtifactMentions>\n"
    "When your reply refers to a Tracecat case, write it as a mention link so the "
    "reader's app can show it: "
    "`[CASE-0142](tracecat-ref://v1/artifact/case/<case id>)`. "
    "The id is the full case UUID from the case tools (`core.cases.*`); never "
    "guess one. "
    "Use mentions only in your final reply, never in tool arguments. "
    "Do not say where the reply will be shown.\n"
    "</ArtifactMentions>"
)

# Sessions whose replies a Tracecat chat renders. MCP and subagent preset runs
# would show a mention as a raw URI; channel sessions are decided per link.
_MENTION_RENDERING_ENTITIES = frozenset(
    {
        AgentSessionEntity.WORKSPACE_CHAT,
        AgentSessionEntity.CASE,
        AgentSessionEntity.AGENT_PRESET_BUILDER,
        AgentSessionEntity.APPROVAL,
    }
)


def artifact_mention_prompt(entity_type: str) -> str | None:
    """The mention prompt for a session, if its replies render mentions."""
    if entity_type in _MENTION_RENDERING_ENTITIES:
        return ARTIFACT_MENTION_PROMPT
    return None
