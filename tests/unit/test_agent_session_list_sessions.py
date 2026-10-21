from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.backends import registry
from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.session.schemas import AgentSessionRead
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role


def _mock_scalar_result(items: list[Any]) -> Mock:
    scalars = Mock()
    scalars.all.return_value = items
    result = Mock()
    result.scalars.return_value = scalars
    return result


def _build_service() -> tuple[AgentSessionService, SimpleNamespace, Role]:
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        scopes=frozenset({"agent:read"}),
    )
    session = SimpleNamespace(execute=AsyncMock())
    return AgentSessionService(cast(Any, session), role), session, role


def _agent_session_row(
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    spawned_by_session_id: uuid.UUID | None,
) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        title="Child approval chat",
        created_by=user_id,
        entity_type="approval",
        entity_id=uuid.uuid4(),
        channel_context=None,
        tools=None,
        mcp_integrations=None,
        agent_preset_id=None,
        agent_preset_version_id=None,
        backend_id="oss",
        agents_binding=None,
        last_error=None,
        artifacts=[],
        harness_type=None,
        last_stream_id=None,
        parent_session_id=None,
        spawned_by_session_id=spawned_by_session_id,
        forked_from_session_id=None,
        created_at=now,
        updated_at=now,
    )


_SOURCE_ID = uuid.uuid4()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "present", "absent"),
    [
        pytest.param(
            {"spawned_by_session_id": _SOURCE_ID},
            ["agent_session.spawned_by_session_id ="],
            [],
            id="spawned-by",
        ),
        pytest.param(
            {"filter_created_by_none": True},
            [
                "agent_session.created_by IS NULL",
                "agent_session.spawned_by_session_id IS NULL",
            ],
            [],
            id="created-by-none",
        ),
        pytest.param(
            {"exclude_entity_types": [AgentSessionEntity.WORKSPACE_CHAT]},
            ["chat.entity_type NOT IN"],
            [],
            id="exclude-legacy-workspace-chats",
        ),
        pytest.param(
            {"forked_from_session_id": _SOURCE_ID, "include_children": True},
            ["agent_session.forked_from_session_id ="],
            ["agent_session.spawned_by_session_id IS NULL"],
            id="fork-source-with-children",
        ),
        pytest.param(
            {"forked_from_session_id": _SOURCE_ID},
            [
                "agent_session.forked_from_session_id =",
                "agent_session.spawned_by_session_id IS NULL",
            ],
            [],
            id="fork-source-standalone",
        ),
    ],
)
async def test_list_sessions_filters(
    kwargs: dict[str, Any], present: list[str], absent: list[str]
) -> None:
    service, db, _role = _build_service()
    db.execute.side_effect = [_mock_scalar_result([]), _mock_scalar_result([])]

    await service.list_sessions(limit=1, **kwargs)

    queries = "\n".join(str(call.args[0]) for call in db.execute.await_args_list)
    for fragment in present:
        assert fragment in queries
    for fragment in absent:
        assert fragment not in queries


@pytest.mark.anyio
async def test_list_sessions_marks_teammate_sessions_read_only() -> None:
    service, session, role = _build_service()
    assert role.workspace_id is not None
    teammate_session = _agent_session_row(
        workspace_id=role.workspace_id,
        user_id=uuid.uuid4(),
        spawned_by_session_id=None,
    )
    session.execute.side_effect = [
        _mock_scalar_result([teammate_session]),
        _mock_scalar_result([]),
    ]

    results = await service.list_sessions(
        created_by=teammate_session.created_by,
        limit=1,
    )

    assert len(results) == 1
    assert results[0].is_readonly is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "backend_state", ["enabled", "disabled", "missing", "unsupported"]
)
@pytest.mark.parametrize("is_child", [False, True], ids=["root", "child"])
async def test_list_sessions_derives_readonly_from_parent_and_backend_state(
    backend_state: str,
    is_child: bool,
) -> None:
    service, db, role = _build_service()
    assert role.workspace_id is not None
    row = _agent_session_row(
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        spawned_by_session_id=uuid.uuid4() if is_child else None,
    )
    row.backend_id = "external"
    row.harness_type = (
        "unsupported" if backend_state == "unsupported" else "claude_code"
    )
    db.execute.side_effect = [
        _mock_scalar_result([row]),
        _mock_scalar_result([]),
    ]
    provider = DefaultBackend()
    with (
        patch.object(
            registry,
            "get_agent_backends",
            return_value={} if backend_state == "missing" else {"external": provider},
        ),
        patch.object(provider, "is_enabled", return_value=backend_state != "disabled"),
    ):
        results = await service.list_sessions(
            spawned_by_session_id=row.spawned_by_session_id
        )
    assert len(results) == 1
    assert isinstance(results[0], AgentSessionRead)
    assert results[0].is_readonly is (is_child or backend_state != "enabled")
