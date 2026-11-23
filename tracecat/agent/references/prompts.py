"""Prompt text that teaches agents the mention format, and which sessions get it."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.channel_notifier import channel_renders_mentions
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.logger import logger

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
    role: Role, db: AsyncSession, agent_session: AgentSession
) -> str | None:
    """The mention prompt, if every place that shows this session's replies renders mentions."""
    if agent_session.entity_type != AgentSessionEntity.EXTERNAL_CHANNEL:
        if agent_session.entity_type in _MENTION_RENDERING_ENTITIES:
            return ARTIFACT_MENTION_PROMPT
        return None
    if not await channel_renders_mentions(role, agent_session.id):
        return None
    # Deferred: the legacy sink imports the Slack handlers, which import the session service.
    from tracecat.agent.channels.sinks.session import streams_to_legacy_channel

    try:
        # A savepoint, so a failed lookup leaves the turn's transaction usable.
        async with db.begin_nested():
            legacy_streams = await streams_to_legacy_channel(db, agent_session)
    except SQLAlchemyError as e:
        logger.warning(
            "Failed to check the legacy channel sink",
            session_id=agent_session.id,
            error=repr(e),
        )
        return None
    # The legacy fan-out streams the raw reply, mentions included.
    return None if legacy_streams else ARTIFACT_MENTION_PROMPT
