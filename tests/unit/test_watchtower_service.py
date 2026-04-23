from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
import tracecat_ee.watchtower.service as watchtower_service
from tracecat_ee.watchtower.service import (
    WatchtowerService,
    _build_agent_fingerprint,
    _sanitize_error_redacted,
    normalize_agent_identity,
    redact_tool_call_args,
)
from tracecat_ee.watchtower.types import WatchtowerAgentStatus, WatchtowerAgentType


class _RowsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _ExecuteResult:
    def __init__(
        self,
        *,
        scalars_rows: list[Any] | None = None,
        tuples_rows: list[tuple[Any, ...]] | None = None,
        scalar_value: Any = None,
    ) -> None:
        self._scalars_rows = scalars_rows or []
        self._tuples_rows = tuples_rows or []
        self._scalar_value = scalar_value

    def scalars(self) -> _RowsResult:
        return _RowsResult(self._scalars_rows)

    def tuples(self) -> _RowsResult:
        return _RowsResult(self._tuples_rows)

    def scalar_one_or_none(self) -> Any:
        return self._scalar_value


class _FakeSession:
    def __init__(self, results: list[_ExecuteResult]) -> None:
        self._results = list(results)
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.committed = False

    async def execute(self, statement: Any) -> _ExecuteResult:
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected execute call")
        return self._results.pop(0)

    async def scalar(self, statement: Any) -> Any:
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected scalar call")
        return self._results.pop(0)._scalar_value

    async def scalars(self, statement: Any) -> _RowsResult:
        self.statements.append(statement)
        if not self._results:
            raise AssertionError("unexpected scalars call")
        return _RowsResult(self._results.pop(0)._scalars_rows)

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        self.committed = True


class _AsyncSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _build_service(fake_session: _FakeSession, org_id: uuid.UUID) -> WatchtowerService:
    role = SimpleNamespace(organization_id=org_id, user_id=uuid.uuid4())
    return WatchtowerService(cast(Any, fake_session), role=cast(Any, role))


def test_normalize_agent_identity_prefers_client_info() -> None:
    agent_type, source, icon = normalize_agent_identity(
        user_agent="Mozilla/5.0",
        client_info={"name": "Claude Code", "version": "1.0.0"},
    )
    assert agent_type == "claude_code"
    assert source == "client_info"
    assert icon == "claude_code"


def test_redact_tool_call_args_does_not_store_raw_strings() -> None:
    result = redact_tool_call_args(
        {
            "workspace_id": "75a17a24-dfd6-45ef-8de0-c15af89f9a72",
            "prompt": "hello world",
            "count": 3,
        }
    )

    args = result["args"]
    assert isinstance(args, dict)
    prompt_meta = args["prompt"]
    assert isinstance(prompt_meta, dict)
    assert prompt_meta["type"] == "str"
    assert prompt_meta["length"] == 11
    assert "hello world" not in str(result)


def test_redact_tool_call_args_summarizes_nested_objects() -> None:
    result = redact_tool_call_args(
        {
            "filters": {
                "status": "active",
                "limit": 25,
            }
        }
    )

    args = result["args"]
    assert isinstance(args, dict)
    filters_meta = args["filters"]
    assert isinstance(filters_meta, dict)
    assert filters_meta["type"] == "object"
    assert filters_meta["key_count"] == 2
    assert filters_meta["keys"] == ["status", "limit"]


def test_redact_tool_call_args_ignores_internal_proxy_metadata() -> None:
    result = redact_tool_call_args(
        {
            "query": "find me",
            "__tracecat": {"tool_call_id": "toolu_123"},
        }
    )

    assert result["arg_count"] == 1
    assert result["keys"] == ["query"]
    args = result["args"]
    assert isinstance(args, dict)
    assert "__tracecat" not in args


def test_sanitize_error_redacted_truncates_long_values() -> None:
    long_message = "x" * 2100
    sanitized = _sanitize_error_redacted(long_message)
    assert sanitized is not None
    assert len(sanitized) == 2000
    assert sanitized.endswith("...")


def test_agent_fingerprint_is_stable_when_client_info_changes() -> None:
    organization_id = uuid.uuid4()
    from_callback = _build_agent_fingerprint(
        organization_id=organization_id,
        auth_client_id="claude-code",
        agent_type=WatchtowerAgentType.CLAUDE_CODE,
        user_agent="Claude-Code/1.2.3",
        client_info=None,
    )
    from_initialize = _build_agent_fingerprint(
        organization_id=organization_id,
        auth_client_id="claude-code",
        agent_type=WatchtowerAgentType.CLAUDE_CODE,
        user_agent="Claude-Code/1.2.3",
        client_info={"name": "Claude Code", "version": "1.2.3"},
    )
    assert from_callback == from_initialize


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_filter", "expected_fragment"),
    [
        (WatchtowerAgentStatus.ACTIVE, "watchtower_agent.last_seen_at >="),
        (WatchtowerAgentStatus.IDLE, "watchtower_agent.last_seen_at <"),
    ],
)
async def test_list_agents_applies_status_window_filters(
    status_filter: WatchtowerAgentStatus,
    expected_fragment: str,
) -> None:
    org_id = uuid.uuid4()
    fake_session = _FakeSession([_ExecuteResult(scalars_rows=[])])
    service = _build_service(fake_session, org_id)

    response = await service.list_agents(
        limit=50,
        cursor=None,
        agent_type=None,
        status=status_filter,
    )

    assert response.items == []
    stmt_text = str(fake_session.statements[0])
    assert "watchtower_agent.blocked_at IS NULL" in stmt_text
    assert expected_fragment in stmt_text


@pytest.mark.anyio
async def test_list_agents_active_session_count_uses_stale_cutoff() -> None:
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    now = datetime.now(UTC)
    agent = SimpleNamespace(
        id=agent_id,
        organization_id=org_id,
        fingerprint_hash="fp",
        agent_type="codex",
        agent_source="user_agent",
        agent_icon_key="codex",
        raw_user_agent="Codex/1.0",
        raw_client_info=None,
        auth_client_id="codex-client",
        last_user_id=None,
        last_user_email="user@example.com",
        last_user_name="User",
        first_seen_at=now - timedelta(minutes=10),
        last_seen_at=now,
        blocked_at=None,
        blocked_reason=None,
    )

    fake_session = _FakeSession(
        [
            _ExecuteResult(tuples_rows=[(agent, 2, 1)]),
        ]
    )
    service = _build_service(fake_session, org_id)

    response = await service.list_agents(
        limit=50,
        cursor=None,
        agent_type=None,
        status=None,
    )

    assert response.items[0].active_session_count == 1
    counts_stmt_text = str(fake_session.statements[0])
    assert "watchtower_agent_session.last_seen_at >=" in counts_stmt_text


@pytest.mark.anyio
async def test_list_agents_dedupes_by_email_and_type() -> None:
    """Regression: list_agents must collapse duplicate fingerprints emitted
    by the MCP OAuth flow into one row per (last_user_email, agent_type)."""
    org_id = uuid.uuid4()
    fake_session = _FakeSession([_ExecuteResult(tuples_rows=[])])
    service = _build_service(fake_session, org_id)

    await service.list_agents(
        limit=10,
        cursor=None,
        agent_type=None,
        status=None,
    )

    from sqlalchemy.dialects import postgresql

    stmt_text = str(fake_session.statements[0].compile(dialect=postgresql.dialect()))
    # Postgres DISTINCT ON over (group_key, agent_type) collapses duplicates.
    assert "DISTINCT ON" in stmt_text, "list_agents must dedup with DISTINCT ON"
    assert "coalesce(watchtower_agent.last_user_email" in stmt_text.lower(), (
        "dedup key must fall back to agent id for NULL emails"
    )
    # Counts must aggregate at the group level so duplicate fingerprints
    # contribute to the canonical row's session counts.
    assert "GROUP BY" in stmt_text
    # The page subquery must be LIMIT-bounded.
    assert "LIMIT" in stmt_text


@pytest.mark.anyio
async def test_disable_agent_does_not_touch_last_seen_at() -> None:
    """Regression: disabling must not bump last_seen_at; otherwise re-enabling
    an idle agent flips it to ACTIVE within the 30-minute stale window."""
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=agent_id,
        last_user_email="user@example.com",
        agent_type="claude_code",
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_value=canonical),  # _load_agent_for_group
            _ExecuteResult(),  # update statement
        ]
    )
    service = _build_service(fake_session, org_id)

    await service.disable_agent(agent_id, reason="testing")

    update_stmt_text = str(fake_session.statements[1])
    assert "blocked_at" in update_stmt_text
    assert "last_seen_at" not in update_stmt_text, (
        "disable_agent must preserve last_seen_at so enable returns to IDLE"
    )


@pytest.mark.anyio
async def test_disable_agent_fans_out_to_siblings() -> None:
    """Disable must apply to every duplicate fingerprint sharing
    (last_user_email, agent_type) so the user-visible state is consistent."""
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=agent_id,
        last_user_email="user@example.com",
        agent_type="claude_code",
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_value=canonical),
            _ExecuteResult(),
        ]
    )
    service = _build_service(fake_session, org_id)

    await service.disable_agent(agent_id, reason=None)

    update_stmt_text = str(fake_session.statements[1])
    # Group fan-out matches by email rather than the canonical id.
    assert "watchtower_agent.last_user_email" in update_stmt_text.lower()


@pytest.mark.anyio
async def test_enable_agent_fans_out_to_siblings() -> None:
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=agent_id,
        last_user_email="user@example.com",
        agent_type="claude_code",
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_value=canonical),
            _ExecuteResult(),
        ]
    )
    service = _build_service(fake_session, org_id)

    await service.enable_agent(agent_id)

    update_stmt_text = str(fake_session.statements[1])
    assert "watchtower_agent.last_user_email" in update_stmt_text.lower()
    assert "blocked_at" in update_stmt_text


@pytest.mark.anyio
async def test_disable_agent_with_null_email_only_affects_canonical() -> None:
    """NULL emails cannot be uniquely grouped, so disable acts on the single
    canonical row only — never a sibling."""
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=agent_id,
        last_user_email=None,
        agent_type="unknown",
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_value=canonical),
            _ExecuteResult(),
        ]
    )
    service = _build_service(fake_session, org_id)

    await service.disable_agent(agent_id, reason=None)

    update_stmt_text = str(fake_session.statements[1])
    assert "watchtower_agent.id" in update_stmt_text.lower()


@pytest.mark.anyio
async def test_list_agent_tool_calls_fans_out_to_sibling_agents() -> None:
    """Tool calls in the side panel must span every duplicate fingerprint of
    the same (email, harness) pair so the unified history is complete."""
    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=agent_id,
        last_user_email="user@example.com",
        agent_type="claude_code",
    )
    fake_session = _FakeSession(
        [
            _ExecuteResult(scalar_value=canonical),  # _load_agent_for_group
            _ExecuteResult(scalars_rows=[]),  # tool calls scan
        ]
    )
    service = _build_service(fake_session, org_id)

    response = await service.list_agent_tool_calls(
        agent_id=agent_id,
        limit=50,
        cursor=None,
        status=None,
    )

    assert response.items == []
    tool_calls_stmt_text = str(fake_session.statements[1])
    # Member resolution joins via last_user_email instead of pinning to
    # canonical's id, so duplicate fingerprints contribute their tool calls.
    assert "last_user_email" in tool_calls_stmt_text.lower()


@pytest.mark.anyio
async def test_oauth_provisional_session_does_not_create_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="user@example.com",
        first_name="Test",
        last_name="User",
    )
    fake_session = _FakeSession([_ExecuteResult(scalar_value=None)])

    async def _resolve_user_by_email(*_args: Any, **_kwargs: Any) -> Any:
        return user

    async def _resolve_unambiguous_org(*_args: Any, **_kwargs: Any) -> uuid.UUID:
        return org_id

    async def _is_org_entitled(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def _prune(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        watchtower_service.config,
        "TRACECAT__EE_MULTI_TENANT",
        True,
    )
    monkeypatch.setattr(
        watchtower_service,
        "get_async_session_context_manager",
        lambda: _AsyncSessionContext(fake_session),
    )
    monkeypatch.setattr(
        watchtower_service, "_resolve_user_by_email", _resolve_user_by_email
    )
    monkeypatch.setattr(
        watchtower_service,
        "_resolve_unambiguous_org",
        _resolve_unambiguous_org,
    )
    monkeypatch.setattr(watchtower_service, "is_org_entitled", _is_org_entitled)
    monkeypatch.setattr(watchtower_service, "prune_watchtower_retention", _prune)

    await watchtower_service.maybe_create_oauth_provisional_session(
        email=user.email,
        auth_client_id="codex-client",
        auth_transaction_id="txn_123",
        user_agent="Mozilla/5.0",
    )

    assert fake_session.committed is True
    assert not any(
        isinstance(instance, watchtower_service.WatchtowerAgent)
        for instance in fake_session.added
    )
    provisional = next(
        instance
        for instance in fake_session.added
        if isinstance(instance, watchtower_service.WatchtowerAgentSession)
    )
    assert provisional.agent_id is None
    assert provisional.session_state == "awaiting_initialize"
