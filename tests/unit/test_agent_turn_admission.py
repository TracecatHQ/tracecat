"""Turn-limit admission in ``run_agent_activity`` and agent usage reads."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.admission.gate import NotAdmittedError
from tracecat.admission.limits import limit_for
from tracecat.admission.types import ConcurrencyResource, NotAdmitted
from tracecat.agent.executor import activity as agent_activity
from tracecat.agent.executor.activity import AgentExecutorResult, run_agent_activity
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.tiers import usage
from tracecat.tiers.enums import ConcurrencyLimitName
from tracecat.tiers.schemas import EffectiveLimits

STARTED = datetime(2026, 1, 1, tzinfo=UTC)


def _input(organization_id: uuid.UUID | None) -> MagicMock:
    agent_input = MagicMock()
    agent_input.role.organization_id = organization_id
    agent_input.timeout_seconds = 600
    return agent_input


@pytest.fixture
def mocks() -> Iterator[SimpleNamespace]:
    info = SimpleNamespace(
        attempt=1,
        activity_id="3",
        workflow_id="agent/session",
        workflow_run_id="run-9",
        task_queue="agent-executor",
        started_time=STARTED,
        scheduled_time=STARTED,
        start_to_close_timeout=timedelta(seconds=900),
        schedule_to_close_timeout=None,
    )
    with (
        patch.object(agent_activity, "activity") as mock_activity,
        patch.object(agent_activity, "set_current_span_attributes"),
        patch.object(agent_activity, "_agent_correlation_attributes", return_value={}),
        patch.object(agent_activity.gate, "acquire", new_callable=AsyncMock) as acquire,
        patch.object(
            agent_activity, "_run_agent_turn", new_callable=AsyncMock
        ) as run_turn,
    ):
        mock_activity.info.return_value = info
        run_turn.return_value = AgentExecutorResult(success=True)
        yield SimpleNamespace(acquire=acquire, run_turn=run_turn)


def _lease() -> MagicMock:
    lease = MagicMock()
    lease.renew = AsyncMock()
    lease.release = AsyncMock()
    return lease


@pytest.mark.anyio
async def test_not_admitted_turn_does_nothing(mocks: SimpleNamespace) -> None:
    org_id = uuid.uuid4()
    mocks.acquire.side_effect = NotAdmittedError(
        resource=ConcurrencyResource.AGENT_TURNS, in_use=2, limit=2
    )

    result = await run_agent_activity(_input(org_id))

    assert result == NotAdmitted(
        resource=ConcurrencyResource.AGENT_TURNS, in_use=2, limit=2, attempt=1
    )
    mocks.run_turn.assert_not_awaited()
    kwargs = mocks.acquire.await_args.kwargs
    assert kwargs["org_id"] == org_id
    assert kwargs["resource"] is ConcurrencyResource.AGENT_TURNS
    assert kwargs["lease_id"] == "run-9:3:1"
    assert kwargs["units"] == 1
    assert kwargs["deadline"] == STARTED + timedelta(seconds=900)


@pytest.mark.anyio
async def test_admitted_turn_runs_then_releases(mocks: SimpleNamespace) -> None:
    lease = _lease()
    mocks.acquire.return_value = lease

    result = await run_agent_activity(_input(uuid.uuid4()))

    assert result == AgentExecutorResult(success=True)
    mocks.run_turn.assert_awaited_once()
    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_failed_turn_still_releases(mocks: SimpleNamespace) -> None:
    lease = _lease()
    mocks.acquire.return_value = lease
    mocks.run_turn.side_effect = RuntimeError("runtime crashed")

    with pytest.raises(RuntimeError):
        await run_agent_activity(_input(uuid.uuid4()))

    lease.release.assert_awaited_once()


@pytest.mark.anyio
async def test_turn_without_an_org_is_not_gated(mocks: SimpleNamespace) -> None:
    await run_agent_activity(_input(None))

    mocks.acquire.assert_not_awaited()
    mocks.run_turn.assert_awaited_once()


def _limits(**overrides: int | None) -> EffectiveLimits:
    values: dict[str, int | None] = {
        "api_rate_limit": None,
        "api_burst_capacity": None,
        "max_concurrent_workflows": None,
        "max_action_executions_per_workflow": None,
        "max_concurrent_actions": None,
        "max_concurrent_tool_calls": None,
        "max_concurrent_agent_turns": None,
    }
    values.update(overrides)
    return EffectiveLimits.model_validate(values)


def test_agent_resources_read_their_tier_columns() -> None:
    limits = _limits(max_concurrent_tool_calls=7, max_concurrent_agent_turns=3)

    assert limit_for(limits, ConcurrencyResource.TOOL_CALLS) == 7
    assert limit_for(limits, ConcurrencyResource.AGENT_TURNS) == 3


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    lease_store = MagicMock()
    lease_store.in_use = AsyncMock(
        side_effect=lambda _org, resource: {
            ConcurrencyResource.TOOL_CALLS: 4,
            ConcurrencyResource.AGENT_TURNS: 1,
        }[resource]
    )
    monkeypatch.setattr(usage.LeaseStore, "create", AsyncMock(return_value=lease_store))
    monkeypatch.setattr(
        usage,
        "is_feature_enabled",
        lambda flag: flag is FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS,
    )
    return lease_store


@pytest.mark.anyio
async def test_usage_reads_leases_for_set_agent_limits(store: MagicMock) -> None:
    result = await usage.read_concurrency_usage(
        uuid.uuid4(),
        _limits(max_concurrent_tool_calls=10, max_concurrent_agent_turns=2),
    )

    assert {name: value.in_use for name, value in result.items()} == {
        ConcurrencyLimitName.MAX_CONCURRENT_TOOL_CALLS: 4,
        ConcurrencyLimitName.MAX_CONCURRENT_AGENT_TURNS: 1,
    }


@pytest.mark.anyio
async def test_usage_is_unknown_when_unlimited(store: MagicMock) -> None:
    result = await usage.read_concurrency_usage(uuid.uuid4(), _limits())

    assert result == {}
    store.in_use.assert_not_awaited()


@pytest.mark.anyio
async def test_usage_is_unknown_when_enforcement_is_off(
    store: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "is_feature_enabled", lambda _flag: False)

    result = await usage.read_concurrency_usage(
        uuid.uuid4(), _limits(max_concurrent_tool_calls=10)
    )

    assert result == {}
    store.in_use.assert_not_awaited()


@pytest.mark.anyio
async def test_usage_is_unknown_when_redis_fails(store: MagicMock) -> None:
    store.in_use.side_effect = ConnectionError("redis down")

    result = await usage.read_concurrency_usage(
        uuid.uuid4(), _limits(max_concurrent_agent_turns=2)
    )

    assert result == {}
