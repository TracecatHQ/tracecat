"""Only sessions whose every reader renders mentions learn to write them."""

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

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


@contextlib.asynccontextmanager
async def _savepoint():
    yield


def _async_db() -> Any:
    db = MagicMock()
    db.begin_nested = _savepoint
    return db


@pytest.mark.parametrize(
    ("entity_type", "expected"),
    [
        (AgentSessionEntity.WORKSPACE_CHAT, ARTIFACT_MENTION_PROMPT),
        (AgentSessionEntity.CASE, ARTIFACT_MENTION_PROMPT),
        (AgentSessionEntity.WORKFLOW, None),
        (AgentSessionEntity.AGENT_PRESET, None),
    ],
)
async def test_web_rendered_sessions_learn_mentions(
    entity_type: AgentSessionEntity, expected: str | None
) -> None:
    assert await artifact_mention_prompt(ROLE, _async_db(), _session(entity_type)) == (
        expected
    )


@pytest.mark.parametrize(
    ("renders", "legacy", "expected"),
    [
        (False, False, None),
        (True, True, None),
        (True, False, ARTIFACT_MENTION_PROMPT),
    ],
    ids=["channel-does-not-render", "legacy-sink-streams-raw", "rendering-channel"],
)
async def test_a_channel_session_learns_mentions_only_where_they_render(
    monkeypatch: pytest.MonkeyPatch, renders: bool, legacy: bool, expected: str | None
) -> None:
    async def channel_renders(*_args: object) -> bool:
        return renders

    async def streams_legacy(*_args: object) -> bool:
        return legacy

    monkeypatch.setattr(prompts, "channel_renders_mentions", channel_renders)
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session.streams_to_legacy_channel",
        streams_legacy,
    )
    session = _session(AgentSessionEntity.EXTERNAL_CHANNEL)

    assert await artifact_mention_prompt(ROLE, _async_db(), session) == expected


async def test_a_failed_legacy_check_teaches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def channel_renders(*_args: object) -> bool:
        return True

    async def broken(*_args: object) -> bool:
        raise SQLAlchemyError("db")

    monkeypatch.setattr(prompts, "channel_renders_mentions", channel_renders)
    monkeypatch.setattr(
        "tracecat.agent.channels.sinks.session.streams_to_legacy_channel", broken
    )
    session = _session(AgentSessionEntity.EXTERNAL_CHANNEL)

    assert await artifact_mention_prompt(ROLE, _async_db(), session) is None
