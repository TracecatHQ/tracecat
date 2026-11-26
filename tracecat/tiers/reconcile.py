"""Apply the tier concurrency limits a deployment declares.

Run as ``python -m tracecat.tiers.reconcile --file limits.json [--dry-run]``.
The Helm chart runs it in a pre-upgrade Job, after migrations, so the limits
are set before new pods start enforcing them.

Only the fields declared for each named tier are written. Other tier fields,
other tiers, and per-organization overrides are left alone. Every name must
match exactly one tier (by display name, ignoring case) or nothing is written.
Admission reads limits through a per-process cache, so running pods pick up
new values after ``TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS``, or up to twice
that while a refresh fails.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Tier
from tracecat.logger import logger
from tracecat.tiers.enums import ConcurrencyLimitName
from tracecat.tiers.exceptions import TierLimitsDeclarationError
from tracecat.tiers.schemas import DeclaredTierLimitsFile
from tracecat.tiers.types import TierLimitChange


async def _resolve_tiers(
    session: AsyncSession, names: Sequence[str]
) -> dict[str, Tier]:
    """Map each declared name to its one matching tier, or raise."""
    keys = [name.lower() for name in names]
    if len(set(keys)) != len(keys):
        raise TierLimitsDeclarationError(
            "Tier names must be unique ignoring case: " + ", ".join(sorted(names))
        )

    # Matched in Python, not with SQL lower(): the two can fold some Unicode
    # names differently, and the tier table holds a handful of rows.
    tiers = (await session.scalars(select(Tier))).all()
    matches: dict[str, list[Tier]] = {key: [] for key in keys}
    for tier in tiers:
        if (key := tier.display_name.lower()) in matches:
            matches[key].append(tier)

    missing = [name for name in names if not matches[name.lower()]]
    if missing:
        raise TierLimitsDeclarationError(
            "No tier with display name: " + ", ".join(missing)
        )
    ambiguous = [name for name in names if len(matches[name.lower()]) > 1]
    if ambiguous:
        raise TierLimitsDeclarationError(
            "More than one tier with display name: " + ", ".join(ambiguous)
        )
    return {name: matches[name.lower()][0] for name in names}


async def reconcile_tier_limits(
    session: AsyncSession,
    declared: DeclaredTierLimitsFile,
    *,
    dry_run: bool = False,
) -> list[TierLimitChange]:
    """Set the declared limits on their tiers and return what changed.

    Args:
        session: Database session. Committed only when something changes and
            ``dry_run`` is off.
        declared: The declared limits, keyed by tier display name.
        dry_run: Compute the changes without writing them.

    Returns:
        One entry per field whose value differs from the declaration.

    Raises:
        TierLimitsDeclarationError: A name matches no tier or several tiers.
            Nothing is written.
    """
    tiers = await _resolve_tiers(session, list(declared.tiers))

    changes: list[TierLimitChange] = []
    for name, limits in declared.tiers.items():
        tier = tiers[name]
        # exclude_unset keeps only declared fields; an explicit null stays.
        for field_name, new in limits.model_dump(exclude_unset=True).items():
            field = ConcurrencyLimitName(field_name)
            # getattr/setattr: the field name is data from a closed enum whose
            # members are all Tier columns.
            old = getattr(tier, field)
            if old == new:
                continue
            changes.append(
                TierLimitChange(
                    tier_name=tier.display_name, field=field, old=old, new=new
                )
            )
            if not dry_run:
                setattr(tier, field, new)

    if changes and not dry_run:
        await session.commit()
        # The record of a deploy-time write: there is no user to audit-log as.
        logger.info("tier_limits.applied", changes=[asdict(c) for c in changes])
    return changes


def _format_limit(value: int | None) -> str:
    return "unlimited" if value is None else str(value)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tracecat.tiers.reconcile",
        description="Apply the tier concurrency limits declared in a JSON file.",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help='JSON file: {"tiers": {"<display name>": {"<field>": <int|null>}}}',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the changes without writing them",
    )
    return parser.parse_args(argv)


async def _run(declared: DeclaredTierLimitsFile, *, dry_run: bool) -> int:
    # A normal session: tier is a global table with no RLS policy.
    async with get_async_session_context_manager() as session:
        changes = await reconcile_tier_limits(session, declared, dry_run=dry_run)

    for change in changes:
        print(
            f"{change.tier_name}.{change.field}: "
            f"{_format_limit(change.old)} -> {_format_limit(change.new)}"
        )
    if not changes:
        print("no changes")
    elif dry_run:
        print(f"dry run: {len(changes)} change(s), nothing written")
    else:
        print(f"applied {len(changes)} change(s)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m tracecat.tiers.reconcile``."""
    args = _parse_args(argv)
    try:
        declared = DeclaredTierLimitsFile.model_validate_json(args.file.read_bytes())
    except (OSError, ValidationError) as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run(declared, dry_run=args.dry_run))
    except TierLimitsDeclarationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
