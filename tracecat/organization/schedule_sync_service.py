from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from temporalio.client import Client

from tracecat.auth.types import AccessLevel, Role
from tracecat.db.models import Schedule, Workflow, Workspace
from tracecat.dsl.client import get_temporal_client
from tracecat.identifiers import ScheduleUUID, WorkflowID, WorkspaceID
from tracecat.organization.schemas import (
    OrgScheduleRecreateAction,
    OrgScheduleRecreateResponse,
    OrgScheduleRecreateResult,
    OrgScheduleTemporalItem,
    OrgScheduleTemporalStatus,
    OrgScheduleTemporalSummary,
    OrgScheduleTemporalSyncRead,
)
from tracecat.service import BaseOrgService
from tracecat.workflow.schedules import bridge

TEMPORAL_CHECK_CONCURRENCY = 20


@dataclass(frozen=True)
class OrganizationScheduleRecord:
    schedule_id: ScheduleUUID
    workspace_id: WorkspaceID
    workspace_name: str
    workflow_id: WorkflowID | None
    workflow_title: str | None
    db_status: Literal["online", "offline"]
    cron: str | None
    every: timedelta | None
    offset: timedelta | None
    start_at: datetime | None
    end_at: datetime | None
    timeout: float | None


def _is_temporal_not_found_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        phrase in message
        for phrase in ("schedule not found", "not found", "does not exist")
    )


def _is_temporal_already_exists_error(error: Exception) -> bool:
    message = str(error).lower()
    return "already exists" in message or "already started" in message


class OrganizationScheduleSyncService(BaseOrgService):
    service_name = "organization_schedule_sync"

    async def _list_schedule_records(
        self,
        schedule_ids: set[ScheduleUUID] | None = None,
    ) -> list[OrganizationScheduleRecord]:
        statement = (
            select(
                Schedule.id.label("schedule_id"),
                Schedule.workspace_id.label("workspace_id"),
                Workspace.name.label("workspace_name"),
                Schedule.workflow_id.label("workflow_id"),
                Workflow.title.label("workflow_title"),
                Schedule.status.label("db_status"),
                Schedule.cron,
                Schedule.every,
                Schedule.offset,
                Schedule.start_at,
                Schedule.end_at,
                Schedule.timeout,
            )
            .join(Workspace, Workspace.id == Schedule.workspace_id)
            .outerjoin(Workflow, Workflow.id == Schedule.workflow_id)
            .where(Workspace.organization_id == self.organization_id)
            .order_by(Workspace.name.asc(), Schedule.workflow_id.asc(), Schedule.id.asc())
        )

        if schedule_ids is not None:
            if not schedule_ids:
                return []
            statement = statement.where(Schedule.id.in_(schedule_ids))

        result = await self.session.execute(statement)
        rows = result.mappings().all()

        records: list[OrganizationScheduleRecord] = []
        for row in rows:
            records.append(
                OrganizationScheduleRecord(
                    schedule_id=ScheduleUUID.new(row["schedule_id"]),
                    workspace_id=row["workspace_id"],
                    workspace_name=row["workspace_name"],
                    workflow_id=(
                        WorkflowID.new(row["workflow_id"])
                        if row["workflow_id"] is not None
                        else None
                    ),
                    workflow_title=row["workflow_title"],
                    db_status=row["db_status"],
                    cron=row["cron"],
                    every=row["every"],
                    offset=row["offset"],
                    start_at=row["start_at"],
                    end_at=row["end_at"],
                    timeout=row["timeout"],
                )
            )
        return records

    async def _check_temporal_schedule(
        self,
        client: Client,
        schedule_id: ScheduleUUID,
    ) -> tuple[OrgScheduleTemporalStatus, str | None]:
        handle = client.get_schedule_handle(schedule_id.to_legacy())
        try:
            await handle.describe()
        except Exception as e:
            if _is_temporal_not_found_error(e):
                return OrgScheduleTemporalStatus.MISSING, None
            return OrgScheduleTemporalStatus.MISSING, str(e)
        return OrgScheduleTemporalStatus.PRESENT, None

    def _build_schedule_runner_role(self, workspace_id: WorkspaceID) -> Role:
        return Role(
            type="service",
            service_id="tracecat-schedule-runner",
            access_level=AccessLevel.ADMIN,
            user_id=None,
            organization_id=self.organization_id,
            workspace_id=workspace_id,
            workspace_role=None,
            org_role=None,
        )

    async def get_temporal_sync_status(self) -> OrgScheduleTemporalSyncRead:
        records = await self._list_schedule_records()
        if not records:
            return OrgScheduleTemporalSyncRead(
                summary=OrgScheduleTemporalSummary(
                    total_schedules=0,
                    present_count=0,
                    missing_count=0,
                ),
                items=[],
            )

        client = await get_temporal_client()
        checked_at = datetime.now(UTC)
        semaphore = asyncio.Semaphore(TEMPORAL_CHECK_CONCURRENCY)

        async def _check_record(
            record: OrganizationScheduleRecord,
        ) -> OrgScheduleTemporalItem:
            async with semaphore:
                temporal_status, error = await self._check_temporal_schedule(
                    client, record.schedule_id
                )
            return OrgScheduleTemporalItem(
                schedule_id=record.schedule_id,
                workspace_id=record.workspace_id,
                workspace_name=record.workspace_name,
                workflow_id=record.workflow_id,
                workflow_title=record.workflow_title,
                db_status=record.db_status,
                temporal_status=temporal_status,
                last_checked_at=checked_at,
                error=error,
            )

        items = await asyncio.gather(*(_check_record(record) for record in records))
        present_count = sum(
            1
            for item in items
            if item.temporal_status == OrgScheduleTemporalStatus.PRESENT
        )
        missing_count = len(items) - present_count

        return OrgScheduleTemporalSyncRead(
            summary=OrgScheduleTemporalSummary(
                total_schedules=len(items),
                present_count=present_count,
                missing_count=missing_count,
            ),
            items=list(items),
        )

    async def recreate_missing_temporal_schedules(
        self,
        schedule_ids: list[ScheduleUUID] | None = None,
    ) -> OrgScheduleRecreateResponse:
        schedule_id_filter = (
            {ScheduleUUID.new(schedule_id) for schedule_id in schedule_ids}
            if schedule_ids is not None
            else None
        )
        records = await self._list_schedule_records(schedule_ids=schedule_id_filter)
        if not records:
            return OrgScheduleRecreateResponse(
                processed_count=0,
                created_count=0,
                already_present_count=0,
                failed_count=0,
                results=[],
            )

        client = await get_temporal_client()
        results: list[OrgScheduleRecreateResult] = []

        for record in records:
            temporal_status, check_error = await self._check_temporal_schedule(
                client, record.schedule_id
            )
            if temporal_status == OrgScheduleTemporalStatus.PRESENT:
                results.append(
                    OrgScheduleRecreateResult(
                        schedule_id=record.schedule_id,
                        action=OrgScheduleRecreateAction.SKIPPED_PRESENT,
                    )
                )
                continue
            if check_error is not None:
                results.append(
                    OrgScheduleRecreateResult(
                        schedule_id=record.schedule_id,
                        action=OrgScheduleRecreateAction.FAILED,
                        error=f"Failed to verify Temporal schedule: {check_error}",
                    )
                )
                continue
            if record.workflow_id is None:
                results.append(
                    OrgScheduleRecreateResult(
                        schedule_id=record.schedule_id,
                        action=OrgScheduleRecreateAction.FAILED,
                        error="Schedule is missing workflow_id in database.",
                    )
                )
                continue

            role = self._build_schedule_runner_role(record.workspace_id)
            paused = record.db_status == "offline"
            try:
                await bridge.create_schedule(
                    workflow_id=record.workflow_id,
                    schedule_id=record.schedule_id,
                    role=role,
                    cron=record.cron,
                    every=record.every,
                    offset=record.offset,
                    start_at=record.start_at,
                    end_at=record.end_at,
                    timeout=record.timeout,
                    paused=paused,
                )
                results.append(
                    OrgScheduleRecreateResult(
                        schedule_id=record.schedule_id,
                        action=OrgScheduleRecreateAction.CREATED,
                    )
                )
            except Exception as e:
                if _is_temporal_already_exists_error(e):
                    results.append(
                        OrgScheduleRecreateResult(
                            schedule_id=record.schedule_id,
                            action=OrgScheduleRecreateAction.SKIPPED_PRESENT,
                        )
                    )
                else:
                    results.append(
                        OrgScheduleRecreateResult(
                            schedule_id=record.schedule_id,
                            action=OrgScheduleRecreateAction.FAILED,
                            error=str(e),
                        )
                    )

        created_count = sum(
            1 for result in results if result.action == OrgScheduleRecreateAction.CREATED
        )
        already_present_count = sum(
            1
            for result in results
            if result.action == OrgScheduleRecreateAction.SKIPPED_PRESENT
        )
        failed_count = sum(
            1 for result in results if result.action == OrgScheduleRecreateAction.FAILED
        )

        return OrgScheduleRecreateResponse(
            processed_count=len(results),
            created_count=created_count,
            already_present_count=already_present_count,
            failed_count=failed_count,
            results=results,
        )
