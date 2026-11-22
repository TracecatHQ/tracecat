"""Set each organization as the fairness key on its existing Temporal schedules.

A Temporal schedule keeps the start action it was created with, so schedules
created before workflow starts carried fairness keys still start unkeyed runs.
Run this once per environment, with the same environment as the API service::

    python -m tracecat.workflow.schedules.fairness_backfill --dry-run
    python -m tracecat.workflow.schedules.fairness_backfill

Every schedule row in the database is matched to its Temporal schedule by ID,
and its start action gets the owning organization's fairness key. Only that
key changes: each write carries the schedule's conflict token, so Temporal
drops it if the schedule changed since it was read, and the schedule is read
again. A schedule whose start action names a different organization than its
database row is reported and left alone. A dry run reads each schedule and
writes nothing. Schedules that already carry their key are left alone, so the
command is safe to interrupt and run again.
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
from temporalio.api.workflow.v1 import NewWorkflowExecutionInfo
from temporalio.api.workflowservice.v1 import (
    DescribeScheduleRequest,
    DescribeScheduleResponse,
    UpdateScheduleRequest,
)
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Schedule, Workspace
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLRunArgs
from tracecat.identifiers import OrganizationID, ScheduleUUID
from tracecat.workflow.executions.common import extract_first

type BackfillOutcome = Literal[
    "updated",
    "already_keyed",
    "missing",
    "unsupported",
    "owner_mismatch",
    "failed",
]

_UPDATE_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 0.5


class ScheduleUpdateConflictError(Exception):
    """The schedule kept changing while the backfill tried to key it."""


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


async def _describe(client: Client, temporal_id: str) -> DescribeScheduleResponse:
    return await client.workflow_service.describe_schedule(
        DescribeScheduleRequest(namespace=client.namespace, schedule_id=temporal_id),
        retry=True,
    )


async def _action_organization_id(
    start: NewWorkflowExecutionInfo,
) -> OrganizationID | None:
    """Return the organization in the role the schedule's runs start with."""
    run_args = DSLRunArgs.model_validate(await extract_first(start.input))
    return run_args.role.organization_id


async def backfill_schedule(
    client: Client, owner: ScheduleOwner, *, dry_run: bool
) -> BackfillOutcome:
    """Give one schedule's start action its organization's fairness key.

    Returns:
        ``"updated"`` when the key was set (or would be, in a dry run),
        ``"already_keyed"`` when it was already set, ``"missing"`` when
        Temporal has no such schedule, ``"unsupported"`` when the action does
        not start a workflow, and ``"owner_mismatch"`` when the action's role
        belongs to another organization than the database row.

    Raises:
        ScheduleUpdateConflictError: The schedule changed between every read
            and write.
    """
    temporal_id = ScheduleUUID.new(owner.schedule_id).to_legacy()
    key = str(owner.organization_id)
    sent_update = False
    for attempt in range(_UPDATE_ATTEMPTS + 1):
        if sent_update:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
        try:
            described = await _describe(client, temporal_id)
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                return "missing"
            raise
        schedule = described.schedule
        if not schedule.action.HasField("start_workflow"):
            return "unsupported"
        start = schedule.action.start_workflow
        # Check the owner first, so a schedule that already carries the
        # row's key but runs as another org is reported, not counted as keyed.
        if await _action_organization_id(start) != owner.organization_id:
            return "owner_mismatch"
        if start.priority.fairness_key == key:
            return "updated" if sent_update else "already_keyed"
        if attempt == _UPDATE_ATTEMPTS:
            break
        if dry_run:
            return "updated"
        # Change only the fairness key; the priority key and weight stay.
        start.priority.fairness_key = key
        # Temporal drops an update whose conflict token is stale, so a pause or
        # edit made after the read is never overwritten. The next read decides.
        await client.workflow_service.update_schedule(
            UpdateScheduleRequest(
                namespace=client.namespace,
                schedule_id=temporal_id,
                schedule=schedule,
                conflict_token=described.conflict_token,
                identity=client.identity,
                request_id=str(uuid.uuid4()),
            ),
            retry=True,
        )
        sent_update = True
    raise ScheduleUpdateConflictError(temporal_id)


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
            print(f"{'failed':<14} {owner.schedule_id}: {type(e).__name__}")
        else:
            if outcome != "already_keyed":
                label = "would set" if dry_run and outcome == "updated" else outcome
                print(f"{label:<14} {owner.schedule_id}")
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
        f"owner mismatch: {counts['owner_mismatch']}, "
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
