"""Per-organization agent cost accounting and monthly budget caps.

Storage layout
--------------

Two ``OrganizationSetting`` keys, JSON-encoded:

- ``agents.monthly_budget_cents`` — int, the per-org cap (absent = unlimited).
- ``agents.usage.{YYYY-MM}`` — one row per UTC calendar month, shaped as
  ``{"total_cents": int, "by_workspace_cents": {workspace_id: cents}}``.

Postgres is the single source of truth for both the cap (read on every agent
launch) and the monthly counter (read for the admin usage-snapshot endpoint).
Agent launches are not high-QPS, so a small indexed SELECT per launch is fine
and lets us avoid the correctness hazards of a best-effort mirror cache.

Source of cost
--------------

Claude Agent SDK's ``ResultMessage.model_usage`` map: ``{route: {"costUSD":
float, ...}}``. We sum ``costUSD`` across entries and round to integer cents
on the way in so no floats hit storage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

import orjson
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import OrganizationSetting
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger
from tracecat.settings.service import SettingsService

MONTHLY_BUDGET_CENTS_KEY = "agents.monthly_budget_cents"
"""OrganizationSetting key storing the per-org monthly budget in cents."""


class UsageRow(NamedTuple):
    """Decoded ``agents.usage.{YYYY-MM}`` row."""

    total_cents: int
    by_workspace_cents: dict[str, int]


def _usage_setting_key(month: str) -> str:
    return f"agents.usage.{month}"


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
# Postgres persistence (durable counter)
# -----------------------------------------------------------------------------


def _decode_usage_row(raw: bytes | None) -> UsageRow:
    """Decode the stored JSON blob into a ``UsageRow``."""
    if raw is None:
        return UsageRow(total_cents=0, by_workspace_cents={})
    try:
        data = orjson.loads(raw)
    except Exception:
        return UsageRow(total_cents=0, by_workspace_cents={})
    total = data.get("total_cents") if isinstance(data, dict) else None
    by_ws_raw = data.get("by_workspace_cents") if isinstance(data, dict) else None
    total_int = int(total) if isinstance(total, (int, float)) else 0
    by_ws: dict[str, int] = {}
    if isinstance(by_ws_raw, dict):
        for k, v in by_ws_raw.items():
            try:
                by_ws[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return UsageRow(total_cents=total_int, by_workspace_cents=by_ws)


def _encode_usage_row(row: UsageRow) -> bytes:
    return orjson.dumps(
        {
            "total_cents": row.total_cents,
            "by_workspace_cents": row.by_workspace_cents,
        },
        option=orjson.OPT_SORT_KEYS,
    )


async def _increment_usage_in_db(
    *,
    org_id: OrganizationID,
    workspace_id: WorkspaceID,
    cost_cents: int,
    month: str,
) -> None:
    """Increment the durable ``agents.usage.{month}`` counter for this org.

    First write of the month is an atomic ``INSERT ... ON CONFLICT DO NOTHING``
    so two racing first-writers don't both try to INSERT and lose one to a
    unique-constraint violation. Subsequent writes lock the existing row with
    ``SELECT ... FOR UPDATE`` and apply the JSON-in-Python merge.
    """
    if cost_cents <= 0:
        return
    ws_field = str(workspace_id)
    key = _usage_setting_key(month)
    initial = UsageRow(
        total_cents=cost_cents, by_workspace_cents={ws_field: cost_cents}
    )

    async with get_async_session_bypass_rls_context_manager() as session:
        insert_stmt = (
            pg_insert(OrganizationSetting)
            .values(
                organization_id=org_id,
                key=key,
                value_type="json",
                value=_encode_usage_row(initial),
                is_encrypted=False,
            )
            .on_conflict_do_nothing(index_elements=["organization_id", "key"])
            .returning(OrganizationSetting.id)
        )
        inserted = (await session.execute(insert_stmt)).scalar_one_or_none()
        if inserted is not None:
            await session.commit()
            return

        sel = (
            select(OrganizationSetting)
            .where(
                OrganizationSetting.organization_id == org_id,
                OrganizationSetting.key == key,
            )
            .with_for_update()
        )
        db_row = (await session.execute(sel)).scalar_one()
        current = _decode_usage_row(db_row.value)
        merged_by_ws = dict(current.by_workspace_cents)
        merged_by_ws[ws_field] = merged_by_ws.get(ws_field, 0) + cost_cents
        updated = UsageRow(
            total_cents=current.total_cents + cost_cents,
            by_workspace_cents=merged_by_ws,
        )
        db_row.value = _encode_usage_row(updated)
        db_row.is_encrypted = False
        await session.commit()


async def _read_usage_from_db(org_id: OrganizationID, month: str) -> UsageRow | None:
    """Read the durable counter. Returns ``None`` if the row doesn't exist."""
    key = _usage_setting_key(month)
    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = select(OrganizationSetting).where(
            OrganizationSetting.organization_id == org_id,
            OrganizationSetting.key == key,
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
    if row is None:
        return None
    return _decode_usage_row(row.value)


# -----------------------------------------------------------------------------
# Cap enforcement + recording
# -----------------------------------------------------------------------------


async def check_monthly_budget(org_id: OrganizationID) -> None:
    """Raise ``BudgetExceededError`` if the current month's spend is at cap."""
    limit = await _load_monthly_budget_cents(org_id)
    if limit is None:
        return

    month = _current_month_utc()
    try:
        durable = await _read_usage_from_db(org_id, month)
    except Exception:
        logger.warning(
            "Budget check DB read failed; allowing run",
            org_id=str(org_id),
        )
        return

    used = durable.total_cents if durable is not None else 0
    if used >= limit:
        raise BudgetExceededError(org_id=org_id, used_cents=used, limit_cents=limit)


def cents_from_usage(usage: dict[str, Any] | None) -> int:
    """Extract cost in integer cents from a merged ``usage`` dict.

    The Claude Code runtime folds the SDK's per-model ``costUSD`` values into
    a single ``total_cost_usd`` field on the merged ``usage`` dict before it
    reaches us, so we just round and scale.
    """
    if not isinstance(usage, dict):
        return 0
    cost = usage.get("total_cost_usd")
    if not isinstance(cost, (int, float)):
        return 0
    return max(0, round(float(cost) * 100))


async def record_agent_cost(
    *,
    org_id: OrganizationID,
    workspace_id: WorkspaceID,
    cost_cents: int,
    session_id: str | None = None,
) -> None:
    """Persist this run's cost to the durable Postgres counter.

    Failures are logged and swallowed — a metering blip must never fail a
    live agent run. The next successful write will include this run's cost
    only if the DB write itself succeeded here; a failed DB write is lost.
    """
    if cost_cents <= 0:
        return

    month = _current_month_utc()
    ws_field = str(workspace_id)

    db_ok = False
    try:
        await _increment_usage_in_db(
            org_id=org_id,
            workspace_id=workspace_id,
            cost_cents=cost_cents,
            month=month,
        )
        db_ok = True
    except SQLAlchemyError:
        logger.warning(
            "Failed to persist agent cost to Postgres",
            org_id=str(org_id),
            workspace_id=ws_field,
            cost_cents=cost_cents,
            session_id=session_id,
        )
    except Exception:
        logger.exception(
            "Unexpected error persisting agent cost to Postgres",
            org_id=str(org_id),
            workspace_id=ws_field,
            cost_cents=cost_cents,
            session_id=session_id,
        )

    logger.info(
        "Recorded agent cost",
        org_id=str(org_id),
        workspace_id=ws_field,
        cost_cents=cost_cents,
        session_id=session_id,
        durable=db_ok,
    )


async def get_usage_snapshot(
    org_id: OrganizationID,
    *,
    month: str | None = None,
) -> OrgUsageSnapshot:
    """Read total + per-workspace breakdown for a UTC month from Postgres."""
    target_month = month or _current_month_utc()

    row: UsageRow | None = None
    try:
        row = await _read_usage_from_db(org_id, target_month)
    except Exception:
        logger.warning(
            "Failed to read Postgres usage snapshot",
            org_id=str(org_id),
            month=target_month,
        )

    limit = await _load_monthly_budget_cents(org_id)
    return OrgUsageSnapshot(
        month_utc=target_month,
        total_cents=row.total_cents if row is not None else 0,
        limit_cents=limit,
        by_workspace_cents=row.by_workspace_cents if row is not None else {},
    )
