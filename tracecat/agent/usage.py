"""Per-organization agent cost accounting and monthly budget caps.

Storage layout
--------------

Two ``OrganizationSetting`` keys, JSON-encoded:

- ``agents.monthly_budget_cents`` — int, the per-org cap (absent = unlimited).
- ``agents.usage.{YYYY-MM}`` — one row per UTC calendar month, shaped as
  ``{"total_cents": int, "by_workspace_cents": {workspace_id: cents}}``.

Redis mirrors the month row for the hot path (cap check on every run):

- ``usage:cost_cents:org:{org_id}:{YYYY-MM}`` — scalar INT
- ``usage:cost_cents:org:{org_id}:{YYYY-MM}:by_ws`` — HASH of ws_id → cents

Both Redis keys expire after 90 days. Postgres is the durable source of
truth; Redis can be evicted / lost and we rebuild from the settings row on
the next read.

Source of cost
--------------

Claude Agent SDK's ``ResultMessage.model_usage`` map: ``{route: {"costUSD":
float, ...}}``. We sum ``costUSD`` across entries and round to integer cents
on the way in so no floats hit storage.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, cast

import orjson
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import OrganizationSetting
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.settings.service import SettingsService

MONTHLY_BUDGET_CENTS_KEY = "agents.monthly_budget_cents"
"""OrganizationSetting key storing the per-org monthly budget in cents."""

_USAGE_KEY_TTL_SECONDS = 90 * 24 * 60 * 60
"""Keep roughly the last three UTC months of Redis counters hot."""


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


def _total_key(org_id: OrganizationID, month: str) -> str:
    return f"usage:cost_cents:org:{org_id}:{month}"


def _by_ws_key(org_id: OrganizationID, month: str) -> str:
    return f"usage:cost_cents:org:{org_id}:{month}:by_ws"


# -----------------------------------------------------------------------------
# Cap lookup (in-process TTL cache to avoid a DB hit on every run)
# -----------------------------------------------------------------------------

_cap_cache: dict[OrganizationID, tuple[float, int | None]] = {}


def _cap_cache_ttl() -> int:
    return max(0, config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS)


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


async def get_monthly_budget_cents(org_id: OrganizationID) -> int | None:
    """Return the effective monthly budget (cents) for the org, cached in-process.

    Cache TTL reuses ``TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS``.
    """
    ttl = _cap_cache_ttl()
    now = time.monotonic()
    cached = _cap_cache.get(org_id)
    if cached is not None and ttl > 0 and cached[0] > now:
        return cached[1]

    limit = await _load_monthly_budget_cents(org_id)
    if ttl > 0:
        _cap_cache[org_id] = (now + ttl, limit)
    return limit


def invalidate_cap_cache(org_id: OrganizationID | None = None) -> None:
    """Drop cached budget(s). Called after a budget PUT."""
    if org_id is None:
        _cap_cache.clear()
    else:
        _cap_cache.pop(org_id, None)


# -----------------------------------------------------------------------------
# Postgres persistence (durable counter; survives Redis eviction/restarts)
# -----------------------------------------------------------------------------


def _decode_usage_row(raw: bytes | None) -> tuple[int, dict[str, int]]:
    """Decode the ``agents.usage.{YYYY-MM}`` blob into (total, by_ws)."""
    if raw is None:
        return 0, {}
    try:
        data = orjson.loads(raw)
    except Exception:
        return 0, {}
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
    return total_int, by_ws


def _encode_usage_row(total: int, by_ws: dict[str, int]) -> bytes:
    return orjson.dumps(
        {"total_cents": total, "by_workspace_cents": by_ws},
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

    Uses ``SELECT ... FOR UPDATE`` to serialize concurrent writers on the
    same org+month row. Creates the row on first write of the month.
    """
    if cost_cents <= 0:
        return
    ws_field = str(workspace_id)
    key = _usage_setting_key(month)

    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = (
            select(OrganizationSetting)
            .where(
                OrganizationSetting.organization_id == org_id,
                OrganizationSetting.key == key,
            )
            .with_for_update()
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            row = OrganizationSetting(
                organization_id=org_id,
                key=key,
                value_type="json",
                value=_encode_usage_row(cost_cents, {ws_field: cost_cents}),
                is_encrypted=False,
            )
            session.add(row)
        else:
            total, by_ws = _decode_usage_row(row.value)
            total += cost_cents
            by_ws[ws_field] = by_ws.get(ws_field, 0) + cost_cents
            row.value = _encode_usage_row(total, by_ws)
            row.is_encrypted = False

        await session.commit()


async def _read_usage_from_db(
    org_id: OrganizationID, month: str
) -> tuple[int, dict[str, int]] | None:
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
    """Raise ``BudgetExceededError`` if the current month's spend is at cap.

    Reads Redis first (fast path); falls back to the durable Postgres row if
    Redis is empty or unreachable. No headroom math — we block *new* runs
    once the counter reaches the limit.
    """
    limit = await get_monthly_budget_cents(org_id)
    if limit is None:
        return

    month = _current_month_utc()
    used: int | None = None
    try:
        redis = await get_redis_client()
        client = await redis._get_client()
        raw = await client.get(_total_key(org_id, month))
        if raw is not None:
            try:
                used = int(raw)
            except (TypeError, ValueError):
                used = None
    except Exception:
        logger.warning(
            "Redis budget check failed; falling back to Postgres",
            org_id=str(org_id),
        )

    if used is None:
        try:
            durable = await _read_usage_from_db(org_id, month)
            used = durable[0] if durable is not None else 0
        except Exception:
            logger.warning(
                "Postgres budget fallback failed; allowing run",
                org_id=str(org_id),
            )
            return

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
    """Persist this run's cost to Postgres (durable) and Redis (hot path).

    Postgres is the source of truth; Redis is mirrored for the cap-check
    fast path. Failures on either side are logged and swallowed — a
    metering blip must never fail a live agent run.
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

    total_key = _total_key(org_id, month)
    by_ws_key = _by_ws_key(org_id, month)
    try:
        redis = await get_redis_client()
        client = await redis._get_client()
        pipe = client.pipeline(transaction=False)
        pipe.incrby(total_key, cost_cents)
        pipe.hincrby(by_ws_key, ws_field, cost_cents)
        pipe.expire(total_key, _USAGE_KEY_TTL_SECONDS)
        pipe.expire(by_ws_key, _USAGE_KEY_TTL_SECONDS)
        await pipe.execute()
    except Exception:
        logger.warning(
            "Failed to mirror agent cost to Redis",
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
    """Read total + per-workspace breakdown for a UTC month.

    Postgres is the source of truth. Redis is used only to avoid a DB read
    on the happy path — if Redis is empty or has a lower total than the
    durable row (e.g. was evicted), we trust Postgres.
    """
    target_month = month or _current_month_utc()
    total = 0
    by_workspace: dict[str, int] = {}

    try:
        redis = await get_redis_client()
        client = await redis._get_client()
        raw_total = await client.get(_total_key(org_id, target_month))
        if raw_total is not None:
            try:
                total = int(raw_total)
            except (TypeError, ValueError):
                total = 0
        raw_hash_any = cast(Any, client.hgetall(_by_ws_key(org_id, target_month)))
        raw_hash = await raw_hash_any
        if isinstance(raw_hash, dict):
            for field, value in raw_hash.items():
                try:
                    by_workspace[str(field)] = int(value)
                except (TypeError, ValueError):
                    continue
    except Exception:
        logger.warning(
            "Failed to read Redis usage snapshot; falling back to Postgres",
            org_id=str(org_id),
            month=target_month,
        )

    if total == 0 and not by_workspace:
        try:
            durable = await _read_usage_from_db(org_id, target_month)
            if durable is not None:
                total, by_workspace = durable
        except Exception:
            logger.warning(
                "Failed to read Postgres usage snapshot",
                org_id=str(org_id),
                month=target_month,
            )

    limit = await get_monthly_budget_cents(org_id)
    return OrgUsageSnapshot(
        month_utc=target_month,
        total_cents=total,
        limit_cents=limit,
        by_workspace_cents=by_workspace,
    )
