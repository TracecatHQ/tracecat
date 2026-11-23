"""Prompt text that teaches agents the mention format, and which sessions get it."""

from tracecat.agent.channel_notifier import channel_renders_mentions
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession

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


async def artifact_mention_prompt(
    role: Role, agent_session: AgentSession
) -> str | None:
    """The mention prompt, if every place that shows this session's replies renders mentions."""
    if agent_session.entity_type != AgentSessionEntity.EXTERNAL_CHANNEL:
        if agent_session.entity_type in _MENTION_RENDERING_ENTITIES:
            return ARTIFACT_MENTION_PROMPT
        return None
    if await channel_renders_mentions(role, agent_session.id):
        return ARTIFACT_MENTION_PROMPT
    return None
