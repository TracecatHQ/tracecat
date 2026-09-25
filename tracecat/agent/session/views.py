"""Build session API views with actor-specific read-only state."""

from tracecat.agent.backends.registry import agent_backend_available
from tracecat.agent.session.schemas import AgentSessionRead
from tracecat.agent.session.types import AgentSessionEntity, is_session_readonly
from tracecat.agent.subagents import ResolvedAgentsConfig
from tracecat.artifacts.projection import validate_artifacts
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession


def build_session_read(session: AgentSession, role: Role) -> AgentSessionRead:
    """Project persisted session data and live backend state for an actor."""
    available = agent_backend_available(session.backend_id, session.harness_type)
    return AgentSessionRead(
        id=session.id,
        workspace_id=session.workspace_id,
        title=session.title,
        created_by=session.created_by,
        is_readonly=(
            session.parent_session_id is not None
            or is_session_readonly(role, session.created_by)
            or not available
        ),
        backend_id=session.backend_id,
        entity_type=AgentSessionEntity(session.entity_type),
        entity_id=session.entity_id,
        channel_context=session.channel_context,
        tools=session.tools,
        mcp_integrations=session.mcp_integrations,
        agent_preset_id=session.agent_preset_id,
        agent_preset_version_id=session.agent_preset_version_id,
        agents_binding=(
            ResolvedAgentsConfig.model_validate(session.agents_binding)
            if session.agents_binding is not None
            else None
        ),
        harness_type=session.harness_type,
        last_error=session.last_error,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_stream_id=session.last_stream_id,
        artifacts=validate_artifacts(session.artifacts),
        parent_session_id=session.parent_session_id,
        forked_from_session_id=session.forked_from_session_id,
    )
