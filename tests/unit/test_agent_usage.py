"""Tests for tracecat/agent/usage.py (monthly budget cap + cost counters)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tracecat.agent import usage as usage_module
from tracecat.agent.usage import UsageRow

FakeDb = dict[tuple[str, str], UsageRow]


@pytest.fixture
def fake_db(
    monkeypatch: pytest.MonkeyPatch,
) -> FakeDb:
    """In-memory stand-in for the durable Postgres usage row.

    Mirrors ``agents.usage.{YYYY-MM}`` keyed by (org_id, month).
    """
    storage: FakeDb = {}

    async def _increment(
        *,
        org_id: uuid.UUID,
        workspace_id: uuid.UUID,
        cost_cents: int,
        month: str,
    ) -> None:
        key = (str(org_id), month)
        current = storage.get(key, UsageRow(total_cents=0, by_workspace_cents={}))
        ws_field = str(workspace_id)
        merged_by_ws = {
            **current.by_workspace_cents,
            ws_field: current.by_workspace_cents.get(ws_field, 0) + cost_cents,
        }
        storage[key] = UsageRow(
            total_cents=current.total_cents + cost_cents,
            by_workspace_cents=merged_by_ws,
        )

    async def _read(org_id: uuid.UUID, month: str) -> UsageRow | None:
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
    fake_db: FakeDb,
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

    durable = fake_db[(str(org_id), fixed_month)]
    assert durable.total_cents == 200
    assert durable.by_workspace_cents[str(ws_a)] == 75
    assert durable.by_workspace_cents[str(ws_b)] == 125


@pytest.mark.anyio
async def test_record_agent_cost_zero_noops(
    fake_db: FakeDb,
    fixed_month: str,
) -> None:
    del fixed_month
    await usage_module.record_agent_cost(
        org_id=uuid.uuid4(), workspace_id=uuid.uuid4(), cost_cents=0
    )
    assert fake_db == {}


# -----------------------------------------------------------------------------
# check_monthly_budget
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_monthly_budget_allows_below_limit(
    fake_db: FakeDb,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, 1000)  # $10.00
    fake_db[(str(org_id), fixed_month)] = UsageRow(
        total_cents=900, by_workspace_cents={str(uuid.uuid4()): 900}
    )
    await usage_module.check_monthly_budget(org_id)


@pytest.mark.anyio
async def test_check_monthly_budget_raises_at_limit(
    fake_db: FakeDb,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, 1000)
    fake_db[(str(org_id), fixed_month)] = UsageRow(
        total_cents=1000, by_workspace_cents={str(uuid.uuid4()): 1000}
    )
    with pytest.raises(usage_module.BudgetExceededError) as excinfo:
        await usage_module.check_monthly_budget(org_id)
    assert excinfo.value.limit_cents == 1000
    assert excinfo.value.used_cents == 1000


@pytest.mark.anyio
async def test_check_monthly_budget_no_limit_is_unlimited(
    fake_db: FakeDb,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    _patch_budget(monkeypatch, None)
    fake_db[(str(org_id), fixed_month)] = UsageRow(
        total_cents=10**9, by_workspace_cents={str(uuid.uuid4()): 10**9}
    )
    await usage_module.check_monthly_budget(org_id)


# -----------------------------------------------------------------------------
# get_usage_snapshot
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_usage_snapshot_returns_total_and_breakdown(
    fake_db: FakeDb,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    ws_a = uuid.uuid4()
    _patch_budget(monkeypatch, 500)
    fake_db[(str(org_id), fixed_month)] = UsageRow(
        total_cents=250, by_workspace_cents={str(ws_a): 250}
    )

    snapshot = await usage_module.get_usage_snapshot(org_id)
    assert snapshot.month_utc == fixed_month
    assert snapshot.total_cents == 250
    assert snapshot.limit_cents == 500
    assert snapshot.by_workspace_cents == {str(ws_a): 250}


@pytest.mark.anyio
async def test_get_usage_snapshot_empty_is_zero(
    fake_db: FakeDb,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_db
    _patch_budget(monkeypatch, None)
    snapshot = await usage_module.get_usage_snapshot(uuid.uuid4())
    assert snapshot.month_utc == fixed_month
    assert snapshot.total_cents == 0
    assert snapshot.by_workspace_cents == {}


# -----------------------------------------------------------------------------
# UTC month rollover
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_current_month_rolls_over_at_utc_boundary(
    fake_db: FakeDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    ws = uuid.uuid4()

    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: "2026-03")
    await usage_module.record_agent_cost(org_id=org_id, workspace_id=ws, cost_cents=10)

    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: "2026-04")
    await usage_module.record_agent_cost(org_id=org_id, workspace_id=ws, cost_cents=20)

    assert fake_db[(str(org_id), "2026-03")].total_cents == 10
    assert fake_db[(str(org_id), "2026-04")].total_cents == 20


@pytest.mark.anyio
async def test_current_month_utc_uses_utc_not_local() -> None:
    value = usage_module._current_month_utc()
    now = datetime.now(UTC)
    assert value == now.strftime("%Y-%m")
