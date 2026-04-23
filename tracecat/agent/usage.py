"""Per-organization agent cost accounting and monthly budget caps.

Storage layout
--------------

- ``OrganizationSetting`` key ``agents.monthly_budget_cents`` holds the per-org
  cap in integer cents (absent = unlimited).
- ``agent_run_cost`` holds one row per completed run: ``(organization_id,
  workspace_id, session_id, cost_usd, created_at)``. Cost is stored as exact
  Numeric USD (5-decimal, i.e. millicent precision) so many cheap runs can
  accumulate without sub-cent losses from per-run rounding. Enforcement
  aggregates via ``SUM(cost_usd)`` and rounds once to integer cents at the
  API boundary. Monthly "reset" is just the date filter rolling over — no
  stored state becomes stale.

Source of cost
--------------

Claude Agent SDK's ``ResultMessage.model_usage`` map: ``{route: {"costUSD":
float, ...}}``. The runtime folds this into a single ``total_cost_usd`` field
on the merged ``usage`` dict; we store it as-is (as ``Decimal``) and only
round to cents when reading aggregates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import AgentRunCost
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger
from tracecat.settings.service import SettingsService

MONTHLY_BUDGET_CENTS_KEY = "agents.monthly_budget_cents"
"""OrganizationSetting key storing the per-org monthly budget in cents."""


class BudgetExceededError(Exception):
    """Raised when the org's current-month spend has reached the cap.

    ``org_id`` is retained as a structured attribute for logging and metrics,
    but intentionally omitted from the message string — that message is
    surfaced to end users via the UI stream, and the raw UUID is not useful
    or relevant to them.
    """

    def __init__(
        self,
        *,
        org_id: OrganizationID,
        used_cents: int,
        limit_cents: int,
    ) -> None:
        self.org_id = org_id
        self.used_cents = used_cents
        self.limit_cents = limit_cents
        super().__init__(
            f"Monthly agent budget reached "
            f"(${used_cents / 100:.2f} of ${limit_cents / 100:.2f} used). "
            "Contact an org admin to raise the cap in "
            "Organization → Agent settings."
        )


class OrgUsageSnapshot(BaseModel):
    """Point-in-time read of an org's monthly agent spend."""

    month_utc: str
    total_cents: int
    limit_cents: int | None
    by_workspace_cents: dict[str, int]


def _current_month_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _now_utc() -> datetime:
    """Module seam for 'now' that honors a pinned ``_current_month_utc``.

    For real runs this is just ``datetime.now(UTC)``. In tests, monkey-patching
    ``_current_month_utc`` also shifts this clock into the corresponding month
    so rows land in the bucket the test is exercising — the same patch controls
    both filter and storage.
    """
    now = datetime.now(UTC)
    pinned = _current_month_utc()
    if pinned == now.strftime("%Y-%m"):
        return now
    year, mm = int(pinned[:4]), int(pinned[5:7])
    return now.replace(year=year, month=mm, day=1)


def _usd_to_cents(usd: Decimal) -> int:
    """Round USD to integer cents using banker's rounding.

    Rounding happens exactly once, on an aggregate — not per run — so no
    sub-cent spend is silently dropped or over-attributed.
    """
    cents = (usd * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return max(0, int(cents))


def _month_bounds_utc(month: str | None = None) -> tuple[datetime, datetime, str]:
    """Return ``(start, end, label)`` for the given (or current) UTC month.

    ``start`` is inclusive, ``end`` is exclusive — suitable for indexed range
    scans on ``created_at``. ``label`` is the ``YYYY-MM`` string used in API
    responses.
    """
    label = month or _current_month_utc()
    year, mm = int(label[:4]), int(label[5:7])
    start = datetime(year, mm, 1, tzinfo=UTC)
    if mm == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, mm + 1, 1, tzinfo=UTC)
    return start, end, label


# -----------------------------------------------------------------------------
# Cap lookup
# -----------------------------------------------------------------------------


async def _load_monthly_budget_cents(org_id: OrganizationID) -> int | None:
    """Read the cap directly from ``OrganizationSetting``.

    Returns None for unlimited (no row, or an unparseable value).
    """
    role = Role(
        type="service",
        user_id=None,
        service_id="tracecat-agent-executor",
        organization_id=org_id,
        workspace_id=None,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )
    try:
        async with SettingsService.with_session(role=role) as service:
            setting = await service.get_org_setting(MONTHLY_BUDGET_CENTS_KEY)
            if setting is None:
                return None
            raw = service.get_value(setting)
    except Exception:
        logger.warning(
            "Failed to read monthly budget; treating as unlimited",
            org_id=str(org_id),
        )
        return None

    if raw is None:
        return None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid monthly_budget_cents value; treating as unlimited",
            org_id=str(org_id),
            raw=raw,
        )
        return None
    return limit if limit > 0 else None


# -----------------------------------------------------------------------------
# Ledger queries
# -----------------------------------------------------------------------------


async def _sum_month_cents(org_id: OrganizationID, month: str) -> int:
    """Sum ``cost_usd`` for an org's runs in the given UTC month, as cents.

    Rounds once on the aggregate so sub-cent per-run spend is preserved in
    storage and only lost (to the nearest cent) at the enforcement boundary.
    """
    start, end, _ = _month_bounds_utc(month)
    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = select(func.coalesce(func.sum(AgentRunCost.cost_usd), 0)).where(
            AgentRunCost.organization_id == org_id,
            AgentRunCost.created_at >= start,
            AgentRunCost.created_at < end,
        )
        total_usd = Decimal((await session.execute(stmt)).scalar_one())
        return _usd_to_cents(total_usd)


async def _workspace_totals(
    org_id: OrganizationID, month: str
) -> tuple[int, dict[str, int]]:
    """Return ``(total_cents, by_workspace_cents)`` for an org in a UTC month."""
    start, end, _ = _month_bounds_utc(month)
    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = (
            select(
                AgentRunCost.workspace_id,
                func.sum(AgentRunCost.cost_usd),
            )
            .where(
                AgentRunCost.organization_id == org_id,
                AgentRunCost.created_at >= start,
                AgentRunCost.created_at < end,
            )
            .group_by(AgentRunCost.workspace_id)
        )
        by_ws: dict[str, int] = {}
        total = 0
        for ws_id, total_usd in (await session.execute(stmt)).all():
            cents_int = _usd_to_cents(Decimal(total_usd or 0))
            by_ws[str(ws_id)] = cents_int
            total += cents_int
        return total, by_ws


# -----------------------------------------------------------------------------
# Cap enforcement + recording
# -----------------------------------------------------------------------------


async def resolve_run_budget(org_id: OrganizationID) -> float | None:
    """Return the per-run dollar cap to pass as SDK ``max_budget_usd``.

    - ``None`` → org is uncapped; the SDK should run without a ceiling.
    - ``float`` → remaining headroom in dollars; the SDK must not exceed it.

    Raises ``BudgetExceededError`` when the org has already reached its cap.
    One ``SUM`` query answers both the gate and the single-run cap.
    """
    limit_cents = await _load_monthly_budget_cents(org_id)
    if limit_cents is None:
        return None

    month = _current_month_utc()
    try:
        used_cents = await _sum_month_cents(org_id, month)
    except Exception:
        logger.warning(
            "Budget check DB read failed; allowing run",
            org_id=str(org_id),
        )
        return None

    if used_cents >= limit_cents:
        raise BudgetExceededError(
            org_id=org_id, used_cents=used_cents, limit_cents=limit_cents
        )
    return (limit_cents - used_cents) / 100


def usd_from_usage(usage: dict[str, Any] | None) -> Decimal | None:
    """Extract exact cost in USD from the runtime-merged ``usage`` dict.

    The Claude Code runtime folds the SDK's per-model ``costUSD`` values into
    a single ``total_cost_usd`` field on the merged ``usage`` dict. We return
    it as a ``Decimal`` so the storage layer can keep sub-cent precision —
    rounding to cents happens once on aggregates, not per run.
    """
    if not isinstance(usage, dict):
        return None
    cost = usage.get("total_cost_usd")
    if not isinstance(cost, (int, float)):
        return None
    # str() round-trips through the shortest float repr, which is as close to
    # the SDK's intended value as we can get without reading their source.
    usd = Decimal(str(cost))
    return usd if usd > 0 else None


def cents_from_usage(usage: dict[str, Any] | None) -> int:
    """Convenience wrapper returning integer cents for display/logging."""
    usd = usd_from_usage(usage)
    return 0 if usd is None else _usd_to_cents(usd)


async def record_agent_cost(
    *,
    org_id: OrganizationID,
    workspace_id: WorkspaceID,
    cost_cents: int | None = None,
    cost_usd: Decimal | None = None,
    session_id: UUID | None = None,
) -> None:
    """Append this run's cost to the durable ledger.

    Pass ``cost_usd`` to preserve sub-cent precision (the runtime path); the
    ``cost_cents`` input is converted exactly and retained for callers that
    only know integer cents.

    Failures are logged and swallowed — a metering blip must never fail a
    live agent run. A failed insert is simply lost from the counter; no
    shared row to merge against and no race to recover from.
    """
    if (cost_usd is None) == (cost_cents is None):
        raise ValueError("Pass exactly one of cost_usd or cost_cents")
    exact_usd = cost_usd if cost_usd is not None else Decimal(cost_cents or 0) / 100
    if exact_usd <= 0:
        return

    ws_field = str(workspace_id)

    db_ok = False
    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            session.add(
                AgentRunCost(
                    organization_id=org_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    cost_usd=exact_usd,
                    # Stamped client-side so callers that pin ``_now_utc``
                    # (tests, and any replay/backfill path) control month
                    # placement deterministically.
                    created_at=_now_utc(),
                )
            )
            await session.commit()
        db_ok = True
    except SQLAlchemyError:
        logger.warning(
            "Failed to persist agent cost to Postgres",
            org_id=str(org_id),
            workspace_id=ws_field,
            cost_usd=str(exact_usd),
            session_id=str(session_id) if session_id else None,
        )
    except Exception:
        logger.exception(
            "Unexpected error persisting agent cost to Postgres",
            org_id=str(org_id),
            workspace_id=ws_field,
            cost_usd=str(exact_usd),
            session_id=str(session_id) if session_id else None,
        )

    logger.info(
        "Recorded agent cost",
        org_id=str(org_id),
        workspace_id=ws_field,
        cost_usd=str(exact_usd),
        session_id=str(session_id) if session_id else None,
        durable=db_ok,
    )


async def get_usage_snapshot(
    org_id: OrganizationID,
    *,
    month: str | None = None,
) -> OrgUsageSnapshot:
    """Read total + per-workspace breakdown for a UTC month from the ledger."""
    target_month = month or _current_month_utc()

    total = 0
    by_workspace: dict[str, int] = {}
    try:
        total, by_workspace = await _workspace_totals(org_id, target_month)
    except Exception:
        logger.warning(
            "Failed to read usage snapshot",
            org_id=str(org_id),
            month=target_month,
        )

    limit = await _load_monthly_budget_cents(org_id)
    return OrgUsageSnapshot(
        month_utc=target_month,
        total_cents=total,
        limit_cents=limit,
        by_workspace_cents=by_workspace,
    )
