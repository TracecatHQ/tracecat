"""Tests for tracecat/agent/usage.py (monthly budget cap + cost counters)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tracecat.agent import usage as usage_module


class _FakePipeline:
    def __init__(self, client: _FakeRedisClient) -> None:
        self._client = client
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def incrby(self, key: str, amount: int) -> _FakePipeline:
        self._ops.append(("incrby", (key, amount)))
        return self

    def hincrby(self, key: str, field: str, amount: int) -> _FakePipeline:
        self._ops.append(("hincrby", (key, field, amount)))
        return self

    def expire(self, key: str, seconds: int) -> _FakePipeline:
        self._ops.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args in self._ops:
            method = getattr(self._client, f"_{name}")
            results.append(method(*args))
        self._ops.clear()
        return results


class _FakeRedisClient:
    def __init__(self) -> None:
        self.ints: dict[str, int] = {}
        self.hashes: dict[str, dict[str, int]] = {}
        self.expires: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        value = self.ints.get(key)
        return None if value is None else str(value)

    async def hgetall(self, key: str) -> dict[str, str]:
        return {k: str(v) for k, v in self.hashes.get(key, {}).items()}

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        del transaction
        return _FakePipeline(self)

    def _incrby(self, key: str, amount: int) -> int:
        self.ints[key] = self.ints.get(key, 0) + amount
        return self.ints[key]

    def _hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + amount
        return bucket[field]

    def _expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


class _FakeRedisWrapper:
    def __init__(self, client: _FakeRedisClient) -> None:
        self._client = client

    async def _get_client(self) -> _FakeRedisClient:
        return self._client


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedisClient:
    client = _FakeRedisClient()
    wrapper = _FakeRedisWrapper(client)

    async def _get_redis_client() -> _FakeRedisWrapper:
        return wrapper

    monkeypatch.setattr(usage_module, "get_redis_client", _get_redis_client)
    return client


@pytest.fixture(autouse=True)
def _reset_cap_cache() -> None:  # pyright: ignore[reportUnusedFunction]
    usage_module.invalidate_cap_cache()


@pytest.fixture
def fake_db(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[tuple[str, str], tuple[int, dict[str, int]]]:
    """In-memory stand-in for the durable Postgres usage row.

    Mirrors ``agents.usage.{YYYY-MM}`` keyed by (org_id, month).
    """
    storage: dict[tuple[str, str], tuple[int, dict[str, int]]] = {}

    async def _increment(
        *,
        org_id: uuid.UUID,
        workspace_id: uuid.UUID,
        cost_cents: int,
        month: str,
    ) -> None:
        key = (str(org_id), month)
        total, by_ws = storage.get(key, (0, {}))
        total += cost_cents
        ws_field = str(workspace_id)
        by_ws = {**by_ws, ws_field: by_ws.get(ws_field, 0) + cost_cents}
        storage[key] = (total, by_ws)

    async def _read(org_id: uuid.UUID, month: str) -> tuple[int, dict[str, int]] | None:
        return storage.get((str(org_id), month))

    monkeypatch.setattr(usage_module, "_increment_usage_in_db", _increment)
    monkeypatch.setattr(usage_module, "_read_usage_from_db", _read)
    return storage


@pytest.fixture
def fixed_month(monkeypatch: pytest.MonkeyPatch) -> str:
    month = "2026-04"

    def _current_month_utc() -> str:
        return month

    monkeypatch.setattr(usage_module, "_current_month_utc", _current_month_utc)
    return month


def _patch_budget(monkeypatch: pytest.MonkeyPatch, cents: int | None) -> None:
    async def _loader(_org_id: uuid.UUID) -> int | None:
        return cents

    monkeypatch.setattr(usage_module, "_load_monthly_budget_cents", _loader)


# -----------------------------------------------------------------------------
# cents_from_usage (reads the merged usage dict produced by the runtime)
# -----------------------------------------------------------------------------


def test_cents_from_usage_rounds_cost_usd_to_cents() -> None:
    # Real shape: runtime folds SDK model_usage costUSD into usage.total_cost_usd.
    usage = {
        "input_tokens": 10,
        "output_tokens": 125,
        "cache_creation_input_tokens": 9321,
        "cache_read_input_tokens": 0,
        "total_cost_usd": 0.012286249999999999,
    }
    assert usage_module.cents_from_usage(usage) == 1


def test_cents_from_usage_handles_multi_model_sum() -> None:
    # Post-merge the runtime has already summed across models.
    usage = {"total_cost_usd": 0.070004}
    assert usage_module.cents_from_usage(usage) == 7


def test_cents_from_usage_empty_or_missing() -> None:
    assert usage_module.cents_from_usage(None) == 0
    assert usage_module.cents_from_usage({}) == 0
    assert usage_module.cents_from_usage({"total_cost_usd": None}) == 0
    # Tokens without cost → still 0 (guardrail is dollar-based)
    assert usage_module.cents_from_usage({"input_tokens": 1000}) == 0


def test_cents_from_usage_rejects_negative() -> None:
    assert usage_module.cents_from_usage({"total_cost_usd": -1.5}) == 0


# -----------------------------------------------------------------------------
# record_agent_cost
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_record_agent_cost_increments_total_and_workspace_hash(
    fake_redis: _FakeRedisClient,
    fake_db: dict[tuple[str, str], tuple[int, dict[str, int]]],
    fixed_month: str,
) -> None:
    org_id = uuid.uuid4()
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()

    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=50
    )
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_b, cost_cents=125
    )
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=25
    )

    total_key = f"usage:cost_cents:org:{org_id}:{fixed_month}"
    by_ws_key = f"usage:cost_cents:org:{org_id}:{fixed_month}:by_ws"

    # Redis mirror
    assert fake_redis.ints[total_key] == 200
    assert fake_redis.hashes[by_ws_key][str(ws_a)] == 75
    assert fake_redis.hashes[by_ws_key][str(ws_b)] == 125
    assert fake_redis.expires[total_key] == 90 * 24 * 60 * 60

    # Postgres source of truth
    durable_total, durable_by_ws = fake_db[(str(org_id), fixed_month)]
    assert durable_total == 200
    assert durable_by_ws[str(ws_a)] == 75
    assert durable_by_ws[str(ws_b)] == 125


@pytest.mark.anyio
async def test_record_agent_cost_zero_noops(
    fake_redis: _FakeRedisClient,
    fake_db: dict[tuple[str, str], tuple[int, dict[str, int]]],
    fixed_month: str,
) -> None:
    await usage_module.record_agent_cost(
        org_id=uuid.uuid4(), workspace_id=uuid.uuid4(), cost_cents=0
    )
    assert fake_redis.ints == {}
    assert fake_redis.hashes == {}
    assert fake_db == {}


# -----------------------------------------------------------------------------
# check_monthly_budget
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_monthly_budget_allows_below_limit(
    fake_redis: _FakeRedisClient,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, 1000)  # $10.00
    fake_redis.ints[f"usage:cost_cents:org:{org_id}:{fixed_month}"] = 900
    await usage_module.check_monthly_budget(org_id)


@pytest.mark.anyio
async def test_check_monthly_budget_raises_at_limit(
    fake_redis: _FakeRedisClient,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, 1000)
    fake_redis.ints[f"usage:cost_cents:org:{org_id}:{fixed_month}"] = 1000
    with pytest.raises(usage_module.BudgetExceededError) as excinfo:
        await usage_module.check_monthly_budget(org_id)
    assert excinfo.value.limit_cents == 1000
    assert excinfo.value.used_cents == 1000


@pytest.mark.anyio
async def test_check_monthly_budget_no_limit_is_unlimited(
    fake_redis: _FakeRedisClient,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, None)
    fake_redis.ints[f"usage:cost_cents:org:{org_id}:{fixed_month}"] = 10**9
    await usage_module.check_monthly_budget(org_id)


@pytest.mark.anyio
async def test_check_monthly_budget_falls_back_to_postgres(
    fake_redis: _FakeRedisClient,
    fake_db: dict[tuple[str, str], tuple[int, dict[str, int]]],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis is empty (e.g. evicted) but Postgres has the real total."""
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, 500)
    # Redis returns nothing — no keys seeded.
    # Postgres has the durable counter with 600 cents already spent.
    fake_db[(str(org_id), fixed_month)] = (600, {str(uuid.uuid4()): 600})

    del fake_redis  # unused: redis is empty by design
    with pytest.raises(usage_module.BudgetExceededError) as excinfo:
        await usage_module.check_monthly_budget(org_id)
    assert excinfo.value.used_cents == 600
    assert excinfo.value.limit_cents == 500


# -----------------------------------------------------------------------------
# get_usage_snapshot
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_usage_snapshot_returns_total_and_breakdown(
    fake_redis: _FakeRedisClient,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    ws_a = uuid.uuid4()
    _patch_budget(monkeypatch, 500)
    fake_redis.ints[f"usage:cost_cents:org:{org_id}:{fixed_month}"] = 250
    fake_redis.hashes[f"usage:cost_cents:org:{org_id}:{fixed_month}:by_ws"] = {
        str(ws_a): 250,
    }

    snapshot = await usage_module.get_usage_snapshot(org_id)
    assert snapshot.month_utc == fixed_month
    assert snapshot.total_cents == 250
    assert snapshot.limit_cents == 500
    assert snapshot.by_workspace_cents == {str(ws_a): 250}


@pytest.mark.anyio
async def test_get_usage_snapshot_empty_is_zero(
    fake_redis: _FakeRedisClient,
    fake_db: dict[tuple[str, str], tuple[int, dict[str, int]]],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_redis, fake_db
    _patch_budget(monkeypatch, None)
    snapshot = await usage_module.get_usage_snapshot(uuid.uuid4())
    assert snapshot.month_utc == fixed_month
    assert snapshot.total_cents == 0
    assert snapshot.by_workspace_cents == {}


@pytest.mark.anyio
async def test_get_usage_snapshot_falls_back_to_postgres(
    fake_redis: _FakeRedisClient,
    fake_db: dict[tuple[str, str], tuple[int, dict[str, int]]],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis empty → snapshot rebuilds from Postgres."""
    org_id = uuid.uuid4()
    ws_a = uuid.uuid4()
    _patch_budget(monkeypatch, 1000)
    fake_db[(str(org_id), fixed_month)] = (750, {str(ws_a): 750})
    del fake_redis  # empty by design

    snapshot = await usage_module.get_usage_snapshot(org_id)
    assert snapshot.total_cents == 750
    assert snapshot.by_workspace_cents == {str(ws_a): 750}
    assert snapshot.limit_cents == 1000


# -----------------------------------------------------------------------------
# UTC month rollover
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_current_month_rolls_over_at_utc_boundary(
    fake_redis: _FakeRedisClient,
    fake_db: dict[tuple[str, str], tuple[int, dict[str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_db
    org_id = uuid.uuid4()
    ws = uuid.uuid4()

    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: "2026-03")
    await usage_module.record_agent_cost(org_id=org_id, workspace_id=ws, cost_cents=10)

    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: "2026-04")
    await usage_module.record_agent_cost(org_id=org_id, workspace_id=ws, cost_cents=20)

    assert fake_redis.ints[f"usage:cost_cents:org:{org_id}:2026-03"] == 10
    assert fake_redis.ints[f"usage:cost_cents:org:{org_id}:2026-04"] == 20


@pytest.mark.anyio
async def test_current_month_utc_uses_utc_not_local() -> None:
    value = usage_module._current_month_utc()
    now = datetime.now(UTC)
    assert value == now.strftime("%Y-%m")


# -----------------------------------------------------------------------------
# Cap cache invalidation
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cap_cache_invalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    values = iter([100, 500])

    async def _loader(_org: uuid.UUID) -> int | None:
        return next(values)

    monkeypatch.setattr(usage_module, "_load_monthly_budget_cents", _loader)
    first = await usage_module.get_monthly_budget_cents(org_id)
    cached = await usage_module.get_monthly_budget_cents(org_id)
    assert first == 100
    assert cached == 100

    usage_module.invalidate_cap_cache(org_id)
    refreshed = await usage_module.get_monthly_budget_cents(org_id)
    assert refreshed == 500
