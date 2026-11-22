"""Set each organization as the fairness key on its existing Temporal schedules.

A Temporal schedule keeps the start action it was created with, so schedules
created before workflow starts carried fairness keys still start unkeyed runs.
Run this once per environment, with the same environment as the API service::

    python -m tracecat.workflow.schedules.fairness_backfill --dry-run
    python -m tracecat.workflow.schedules.fairness_backfill

Every schedule row in the database is matched to its Temporal schedule by ID,
and its start action gets the owning organization's fairness key. A dry run
reads each schedule and writes nothing. Schedules that already carry their
key are left alone, so the command is safe to interrupt and run again.
"""

import argparse
import asyncio
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import (
    Client,
    ScheduleActionStartWorkflow,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.service import RPCError, RPCStatusCode

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Schedule, Workspace
from tracecat.dsl.client import get_temporal_client
from tracecat.identifiers import OrganizationID, ScheduleUUID
from tracecat.temporal.fairness import org_priority

type BackfillOutcome = Literal[
    "updated", "already_keyed", "missing", "unsupported", "failed"
]


@dataclass(frozen=True, slots=True)
class ScheduleOwner:
    """A schedule and the organization whose fairness key it should carry."""

    schedule_id: uuid.UUID
    organization_id: OrganizationID


async def list_schedule_owners(session: AsyncSession) -> list[ScheduleOwner]:
    """List every schedule with its owning organization, across all orgs."""
    stmt = (
        select(Schedule.id, Workspace.organization_id)
        .join(Workspace, Workspace.id == Schedule.workspace_id)
        .order_by(Schedule.id)
    )
    result = await session.execute(stmt)
    return [
        ScheduleOwner(schedule_id=schedule_id, organization_id=organization_id)
        for schedule_id, organization_id in result.tuples().all()
    ]


async def backfill_schedule(
    client: Client, owner: ScheduleOwner, *, dry_run: bool
) -> BackfillOutcome:
    """Give one schedule's start action its organization's fairness key.

    Returns:
        ``"updated"`` when the key was set (or would be, in a dry run),
        ``"already_keyed"`` when it was already set, ``"missing"`` when
        Temporal has no such schedule, and ``"unsupported"`` when the action
        does not start a workflow.
    """
    temporal_id = ScheduleUUID.new(owner.schedule_id).to_legacy()
    handle = client.get_schedule_handle(temporal_id)
    outcome: BackfillOutcome = "already_keyed"

    def keyed_update(input: ScheduleUpdateInput) -> ScheduleUpdate | None:
        nonlocal outcome
        schedule = input.description.schedule
        action = schedule.action
        if not isinstance(action, ScheduleActionStartWorkflow):
            outcome = "unsupported"
            return None
        priority = org_priority(owner.organization_id, action.priority)
        if action.priority == priority:
            outcome = "already_keyed"
            return None
        outcome = "updated"
        if dry_run:
            # Returning no update makes Temporal skip the write.
            return None
        action.priority = priority
        return ScheduleUpdate(schedule=schedule)

    try:
        await handle.update(keyed_update)
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return "missing"
        raise
    return outcome


async def backfill_schedule_fairness_keys(
    client: Client, owners: Sequence[ScheduleOwner], *, dry_run: bool
) -> Counter[BackfillOutcome]:
    """Backfill every schedule in ``owners`` and count the outcomes.

    A failure on one schedule is counted and reported, and the run continues.
    """
    counts: Counter[BackfillOutcome] = Counter()
    for owner in owners:
        try:
            outcome = await backfill_schedule(client, owner, dry_run=dry_run)
        except Exception as e:
            outcome = "failed"
            print(f"failed      {owner.schedule_id}: {type(e).__name__}")
        else:
            if outcome != "already_keyed":
                label = "would set" if dry_run and outcome == "updated" else outcome
                print(f"{label:<11} {owner.schedule_id}")
        counts[outcome] += 1
    return counts


async def _run_cli(args: argparse.Namespace) -> int:
    async with get_async_session_bypass_rls_context_manager() as session:
        owners = await list_schedule_owners(session)
    client = await get_temporal_client()
    print(
        f"Schedules in the database: {len(owners)}"
        f"{' (dry run)' if args.dry_run else ''}"
    )
    counts = await backfill_schedule_fairness_keys(client, owners, dry_run=args.dry_run)
    updated_label = "would update" if args.dry_run else "updated"
    print(
        f"{updated_label}: {counts['updated']}, "
        f"already keyed: {counts['already_keyed']}, "
        f"missing in Temporal: {counts['missing']}, "
        f"unsupported action: {counts['unsupported']}, "
        f"failed: {counts['failed']}"
    )
    return 1 if counts["failed"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tracecat.workflow.schedules.fairness_backfill",
        description=(
            "Set each organization as the Temporal fairness key on its "
            "existing workflow schedules."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the schedules that would change without writing.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
