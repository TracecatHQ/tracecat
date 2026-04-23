"""Tests for tracecat/agent/usage.py (monthly budget cap + per-run cost ledger)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent import usage as usage_module
from tracecat.db.models import Organization, Workspace


@pytest.fixture
def fixed_month(monkeypatch: pytest.MonkeyPatch) -> str:
    """Freeze ``_current_month_utc`` for deterministic month_utc values."""
    month = "2026-04"
    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: month)
    return month


@pytest.fixture
async def session_ctx(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncSession:
    """Route ``usage``'s DB accesses through the test's savepoint-scoped session.

    The module opens its own sessions via ``get_async_session_bypass_rls_context_manager``;
    we replace that with a context manager that yields the test session so all
    reads and writes land in the same transactional scope (rolled back on teardown).
    """

    @asynccontextmanager
    async def _fake_session_manager() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        usage_module,
        "get_async_session_bypass_rls_context_manager",
        _fake_session_manager,
    )
    return session


@pytest.fixture
async def org_and_workspace(
    session_ctx: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an ``Organization`` + ``Workspace`` visible to the test transaction.

    Both are required for the ``agent_run_cost`` FK constraints; flushing (not
    committing) makes them visible within the test's SAVEPOINT and they roll
    back at teardown with everything else.
    """
    org = Organization(
        id=uuid.uuid4(),
        name="usage test org",
        slug=f"usage-test-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session_ctx.add(org)
    await session_ctx.flush()

    ws = Workspace(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="usage test ws",
        settings={},
    )
    session_ctx.add(ws)
    await session_ctx.flush()

    return org.id, ws.id


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
# record_agent_cost + ledger queries
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_record_agent_cost_appends_and_aggregates(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    fixed_month: str,
) -> None:
    """Three runs → three rows; SUM aggregates totals and GROUP BY splits by ws."""
    org_id, ws_a = org_and_workspace

    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=50
    )
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=125
    )
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=25
    )

    total = await usage_module._sum_month_cents(org_id, fixed_month)
    assert total == 200

    snapshot_total, by_ws = await usage_module._workspace_totals(org_id, fixed_month)
    assert snapshot_total == 200
    assert by_ws == {str(ws_a): 200}


@pytest.mark.anyio
async def test_record_agent_cost_zero_is_noop(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    fixed_month: str,
) -> None:
    org_id, ws_a = org_and_workspace
    await usage_module.record_agent_cost(org_id=org_id, workspace_id=ws_a, cost_cents=0)
    assert await usage_module._sum_month_cents(org_id, fixed_month) == 0


# -----------------------------------------------------------------------------
# resolve_run_budget
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_run_budget_returns_remaining_dollars(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixed_month
    org_id, ws_a = org_and_workspace
    _patch_budget(monkeypatch, 1000)  # $10.00 cap
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=900
    )
    # $10 cap, $9 spent → $1 headroom for this run.
    assert await usage_module.resolve_run_budget(org_id) == pytest.approx(1.0)


@pytest.mark.anyio
async def test_resolve_run_budget_raises_at_limit(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixed_month
    org_id, ws_a = org_and_workspace
    _patch_budget(monkeypatch, 1000)
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=1000
    )
    with pytest.raises(usage_module.BudgetExceededError) as excinfo:
        await usage_module.resolve_run_budget(org_id)
    assert excinfo.value.limit_cents == 1000
    assert excinfo.value.used_cents == 1000


@pytest.mark.anyio
async def test_resolve_run_budget_no_cap_returns_none(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fixed_month
    org_id, ws_a = org_and_workspace
    _patch_budget(monkeypatch, None)
    # Any amount of spend — uncapped org always gets None.
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=10_000
    )
    assert await usage_module.resolve_run_budget(org_id) is None


# -----------------------------------------------------------------------------
# get_usage_snapshot
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_usage_snapshot_returns_total_and_breakdown(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    session_ctx: AsyncSession,
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id, ws_a = org_and_workspace

    # Second workspace under the same org to prove the GROUP BY splits correctly.
    ws_b = uuid.uuid4()
    session_ctx.add(
        Workspace(id=ws_b, organization_id=org_id, name="ws-b", settings={})
    )
    await session_ctx.flush()

    _patch_budget(monkeypatch, 500)
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=100
    )
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_b, cost_cents=150
    )

    snapshot = await usage_module.get_usage_snapshot(org_id)
    assert snapshot.month_utc == fixed_month
    assert snapshot.total_cents == 250
    assert snapshot.limit_cents == 500
    assert snapshot.by_workspace_cents == {str(ws_a): 100, str(ws_b): 150}


@pytest.mark.anyio
async def test_get_usage_snapshot_empty_is_zero(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    fixed_month: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id, _ = org_and_workspace
    _patch_budget(monkeypatch, None)
    snapshot = await usage_module.get_usage_snapshot(org_id)
    assert snapshot.month_utc == fixed_month
    assert snapshot.total_cents == 0
    assert snapshot.by_workspace_cents == {}


# -----------------------------------------------------------------------------
# UTC month rollover — nothing to "reset", the filter just changes
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_month_filter_rolls_over_at_utc_boundary(
    org_and_workspace: tuple[uuid.UUID, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id, ws_a = org_and_workspace

    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: "2026-03")
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=10
    )

    monkeypatch.setattr(usage_module, "_current_month_utc", lambda: "2026-04")
    await usage_module.record_agent_cost(
        org_id=org_id, workspace_id=ws_a, cost_cents=20
    )

    assert await usage_module._sum_month_cents(org_id, "2026-03") == 10
    assert await usage_module._sum_month_cents(org_id, "2026-04") == 20


def test_current_month_utc_uses_utc_not_local() -> None:
    value = usage_module._current_month_utc()
    now = datetime.now(UTC)
    assert value == now.strftime("%Y-%m")
