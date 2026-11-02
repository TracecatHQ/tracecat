from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import tracecat_ee.watchtower.service as watchtower_service
from tracecat_ee.watchtower.service import (
    _build_agent_fingerprint,
    _sanitize_error_redacted,
    normalize_agent_identity,
    redact_tool_call_args,
)
from tracecat_ee.watchtower.types import WatchtowerAgentType


class _ExecuteResult:
    def __init__(self, *, scalar_value: Any = None) -> None:
        self._scalar_value = scalar_value

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


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _WorkspaceOrgSession:
    """Answers ``select(Workspace.organization_id)`` with a fixed org."""

    def __init__(self, workspace_org_id: uuid.UUID | None) -> None:
        self.workspace_org_id = workspace_org_id
        self.calls = 0

    async def execute(self, _statement: Any) -> _ScalarResult:
        self.calls += 1
        return _ScalarResult(self.workspace_org_id)


@pytest.mark.anyio
async def test_resolve_candidate_orgs_single_org_token_matches_unambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()

    async def _resolve_unambiguous_org(*_args: Any, **kwargs: Any) -> uuid.UUID:
        assert kwargs["claimed_org_ids"] == frozenset({org_id})
        return org_id

    monkeypatch.setattr(
        watchtower_service, "_resolve_unambiguous_org", _resolve_unambiguous_org
    )

    result = await watchtower_service._resolve_candidate_orgs(
        _WorkspaceOrgSession(None),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        claimed_org_ids=frozenset({org_id}),
        claimed_workspace_ids=None,
        target_workspace_id=uuid.uuid4(),
    )

    assert result == [org_id]


@pytest.mark.anyio
async def test_resolve_candidate_orgs_multi_org_token() -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    claimed = frozenset({org_a, org_b})
    expected_all = sorted(claimed, key=str)

    # No target: every granted org applies.
    assert (
        await watchtower_service._resolve_candidate_orgs(
            _WorkspaceOrgSession(None),  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            claimed_org_ids=claimed,
            claimed_workspace_ids=None,
        )
        == expected_all
    )
    # Workspace in a granted org narrows to that org.
    assert await watchtower_service._resolve_candidate_orgs(
        _WorkspaceOrgSession(org_b),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        claimed_org_ids=claimed,
        claimed_workspace_ids=None,
        target_workspace_id=uuid.uuid4(),
    ) == [org_b]
    # Explicit org_id argument narrows to that org.
    assert await watchtower_service._resolve_candidate_orgs(
        _WorkspaceOrgSession(None),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        claimed_org_ids=claimed,
        claimed_workspace_ids=None,
        target_organization_id=org_a,
    ) == [org_a]
    # Targets outside the token's orgs never widen or narrow the check.
    assert (
        await watchtower_service._resolve_candidate_orgs(
            _WorkspaceOrgSession(uuid.uuid4()),  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            claimed_org_ids=claimed,
            claimed_workspace_ids=None,
            target_workspace_id=uuid.uuid4(),
            target_organization_id=uuid.uuid4(),
        )
        == expected_all
    )


def _patch_tool_call_lookup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidates: list[uuid.UUID],
    per_org: dict[uuid.UUID, tuple[Any, str | None]],
) -> list[uuid.UUID]:
    checked: list[uuid.UUID] = []

    async def _resolve_user_by_email(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(id=uuid.uuid4())

    async def _resolve_candidate_orgs(*_args: Any, **_kwargs: Any) -> list[uuid.UUID]:
        return candidates

    async def _for_org(
        _session: Any, *, organization_id: uuid.UUID, mcp_session_id: str
    ) -> tuple[Any, str | None]:
        checked.append(organization_id)
        return per_org[organization_id]

    monkeypatch.setattr(watchtower_service.config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        watchtower_service,
        "get_async_session_context_manager",
        lambda: _AsyncSessionContext(_FakeSession([])),
    )
    monkeypatch.setattr(
        watchtower_service, "_resolve_user_by_email", _resolve_user_by_email
    )
    monkeypatch.setattr(
        watchtower_service, "_resolve_candidate_orgs", _resolve_candidate_orgs
    )
    monkeypatch.setattr(watchtower_service, "_get_tool_call_context_for_org", _for_org)
    return checked


@pytest.mark.anyio
async def test_tool_call_context_blocks_when_any_granted_org_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    ctx_a, ctx_b = object(), object()
    checked = _patch_tool_call_lookup(
        monkeypatch,
        candidates=[org_a, org_b],
        per_org={org_a: (ctx_a, None), org_b: (ctx_b, "disabled by admin")},
    )

    result = await watchtower_service.get_watchtower_tool_call_context(
        email="user@example.com",
        mcp_session_id="session-1",
        claimed_org_ids=frozenset({org_a, org_b}),
        claimed_workspace_ids=None,
    )

    assert result == (ctx_b, "disabled by admin")
    assert checked == [org_a, org_b]


@pytest.mark.anyio
async def test_tool_call_context_returns_first_tracked_org_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    ctx_b = object()
    _patch_tool_call_lookup(
        monkeypatch,
        candidates=[org_a, org_b],
        per_org={org_a: (None, None), org_b: (ctx_b, None)},
    )

    result = await watchtower_service.get_watchtower_tool_call_context(
        email="user@example.com",
        mcp_session_id="session-1",
        claimed_org_ids=frozenset({org_a, org_b}),
        claimed_workspace_ids=None,
    )

    assert result == (ctx_b, None)


@pytest.mark.anyio
async def test_initialize_event_registers_session_in_each_entitled_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_a, org_b, org_unentitled = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), email="user@example.com")
    fake_session = _FakeSession([])
    ingested: list[uuid.UUID] = []

    async def _resolve_user_by_email(*_args: Any, **_kwargs: Any) -> Any:
        return user

    async def _resolve_candidate_orgs(*_args: Any, **_kwargs: Any) -> list[uuid.UUID]:
        return [org_a, org_unentitled, org_b]

    async def _is_org_entitled(_session: Any, org_id: uuid.UUID, *_args: Any) -> bool:
        return org_id != org_unentitled

    async def _ingest(_session: Any, *, organization_id: uuid.UUID, **_kwargs: Any):
        ingested.append(organization_id)
        return SimpleNamespace(organization_id=organization_id)

    monkeypatch.setattr(watchtower_service.config, "TRACECAT__EE_MULTI_TENANT", True)
    monkeypatch.setattr(
        watchtower_service,
        "get_async_session_context_manager",
        lambda: _AsyncSessionContext(fake_session),
    )
    monkeypatch.setattr(
        watchtower_service, "_resolve_user_by_email", _resolve_user_by_email
    )
    monkeypatch.setattr(
        watchtower_service, "_resolve_candidate_orgs", _resolve_candidate_orgs
    )
    monkeypatch.setattr(watchtower_service, "is_org_entitled", _is_org_entitled)
    monkeypatch.setattr(watchtower_service, "_ingest_initialize_for_org", _ingest)

    result = await watchtower_service.ingest_watchtower_initialize_event(
        email=user.email,
        auth_client_id="client",
        mcp_session_id="session-1",
        user_agent=None,
        client_info=None,
        claimed_org_ids=frozenset({org_a, org_b, org_unentitled}),
        claimed_workspace_ids=None,
    )

    assert ingested == [org_a, org_b]
    assert result is not None and result.organization_id == org_a
    assert fake_session.committed is True
