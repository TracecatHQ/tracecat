"""Only sessions whose every reader renders mentions learn to write them."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from tracecat.agent.references import prompts
from tracecat.agent.references.prompts import (
    ARTIFACT_MENTION_PROMPT,
    artifact_mention_prompt,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role

pytestmark = pytest.mark.anyio

ROLE = Role(type="service", service_id="tracecat-api", workspace_id=uuid.uuid4())


def _session(entity_type: AgentSessionEntity) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), entity_type=entity_type.value)


@pytest.mark.parametrize(
    ("entity_type", "expected"),
    [
        (AgentSessionEntity.WORKSPACE_CHAT, ARTIFACT_MENTION_PROMPT),
        (AgentSessionEntity.CASE, ARTIFACT_MENTION_PROMPT),
        (AgentSessionEntity.AGENT_PRESET_BUILDER, ARTIFACT_MENTION_PROMPT),
        (AgentSessionEntity.APPROVAL, ARTIFACT_MENTION_PROMPT),
        (AgentSessionEntity.WORKFLOW, None),
        (AgentSessionEntity.AGENT_PRESET, None),
    ],
)
async def test_web_rendered_sessions_learn_mentions(
    entity_type: AgentSessionEntity, expected: str | None
) -> None:
    assert await artifact_mention_prompt(ROLE, _session(entity_type)) == expected


@pytest.mark.parametrize(
    ("renders", "expected"),
    [(False, None), (True, ARTIFACT_MENTION_PROMPT)],
    ids=["channel-does-not-render", "rendering-channel"],
)
async def test_a_channel_session_learns_mentions_only_where_they_render(
    monkeypatch: pytest.MonkeyPatch, renders: bool, expected: str | None
) -> None:
    async def channel_renders(*_args: object) -> bool:
        return renders

    monkeypatch.setattr(prompts, "channel_renders_mentions", channel_renders)
    session = _session(AgentSessionEntity.EXTERNAL_CHANNEL)

    assert await artifact_mention_prompt(ROLE, session) == expected
