"""Services for AI SPM APIs."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    SpmAsset,
    SpmAssetSighting,
    SpmEndpoint,
    SpmEnforcementTask,
    SpmFinding,
    SpmFindingDecision,
)
from tracecat.exceptions import EntitlementRequired, TracecatNotFoundError
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseOrgService
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat_ee.spm.schemas import (
    SpmAssetRead,
    SpmEndpointCreate,
    SpmEndpointCreateResponse,
    SpmEndpointRead,
    SpmEndpointSyncRequest,
    SpmEndpointSyncResponse,
    SpmEnforcementTaskRead,
    SpmFindingDecisionCreate,
    SpmFindingDecisionRead,
    SpmFindingRead,
    SpmSyncAssetUpsert,
    SpmSyncTaskResult,
)
from tracecat_ee.spm.types import (
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmSyncTaskResultStatus,
)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _apply_desc_cursor_filter(
    stmt: sa.Select[Any],
    *,
    model: type[Any],
    cursor: str | None,
    sort_attr: str,
) -> sa.Select[Any]:
    if not cursor:
        return stmt
    cursor_data = BaseCursorPaginator.decode_cursor(cursor)
    sort_value = cursor_data.sort_value
    if sort_value is None:
        raise ValueError("Cursor missing sort value")
    cursor_id = uuid.UUID(cursor_data.id)
    sort_column = getattr(model, sort_attr)
    return stmt.where(
        sa.or_(
            sort_column < sort_value,
            sa.and_(sort_column == sort_value, model.id < cursor_id),
        )
    )


class SpmService(BaseOrgService):
    """Org-scoped service for SPM operator APIs."""

    service_name = "spm"

    async def list_endpoints(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SpmEndpointRead]:
        stmt = select(SpmEndpoint).where(
            SpmEndpoint.organization_id == self.organization_id
        )
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmEndpoint,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(SpmEndpoint.updated_at.desc(), SpmEndpoint.id.desc())
        stmt = stmt.limit(params.limit + 1)

        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        return CursorPaginatedResponse[SpmEndpointRead](
            items=SpmEndpointRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def create_endpoint(
        self,
        params: SpmEndpointCreate,
    ) -> SpmEndpointCreateResponse:
        enrollment_token = _issue_secret("tcspm_enroll")
        row = SpmEndpoint(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            name=params.name,
            harness=params.harness.value,
            platform=params.platform.value,
            status="pending",
            hostname=params.hostname,
            os_user=params.os_user,
            home_path=params.home_path,
            endpoint_version=params.endpoint_version,
            client_metadata=params.client_metadata,
            enrollment_token_hash=_hash_token(enrollment_token),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return SpmEndpointCreateResponse(
            endpoint=SpmEndpointRead.model_validate(row),
            enrollment_token=enrollment_token,
        )

    async def get_endpoint(self, endpoint_id: uuid.UUID) -> SpmEndpointRead:
        row = await self._get_endpoint_row(endpoint_id)
        return SpmEndpointRead.model_validate(row)

    async def list_assets(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SpmAssetRead]:
        stmt = select(SpmAsset).where(SpmAsset.organization_id == self.organization_id)
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmAsset,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(SpmAsset.updated_at.desc(), SpmAsset.id.desc())
        stmt = stmt.limit(params.limit + 1)
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        return CursorPaginatedResponse[SpmAssetRead](
            items=SpmAssetRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get_asset(self, asset_id: uuid.UUID) -> SpmAssetRead:
        stmt = select(SpmAsset).where(
            SpmAsset.id == asset_id,
            SpmAsset.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"SPM asset not found: {asset_id}")
        return SpmAssetRead.model_validate(row)

    async def list_findings(
        self,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SpmFindingRead]:
        stmt = select(SpmFinding).where(
            SpmFinding.organization_id == self.organization_id
        )
        stmt = _apply_desc_cursor_filter(
            stmt,
            model=SpmFinding,
            cursor=params.cursor,
            sort_attr="updated_at",
        )
        stmt = stmt.order_by(SpmFinding.updated_at.desc(), SpmFinding.id.desc())
        stmt = stmt.limit(params.limit + 1)
        rows = list((await self.session.scalars(stmt)).all())
        has_more = len(rows) > params.limit
        if has_more:
            rows = rows[: params.limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = BaseCursorPaginator.encode_cursor(
                id=last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        return CursorPaginatedResponse[SpmFindingRead](
            items=SpmFindingRead.list_adapter().validate_python(rows),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get_finding(self, finding_id: uuid.UUID) -> SpmFindingRead:
        row = await self._get_finding_row(finding_id)
        return SpmFindingRead.model_validate(row)

    async def create_finding_decision(
        self,
        finding_id: uuid.UUID,
        params: SpmFindingDecisionCreate,
    ) -> SpmFindingDecisionRead:
        finding = await self._get_finding_row(finding_id)
        decision_row = SpmFindingDecision(
            id=uuid.uuid4(),
            organization_id=self.organization_id,
            finding_id=finding.id,
            endpoint_id=finding.endpoint_id,
            decision=params.decision.value,
            reason=params.reason,
            payload=params.payload,
            decided_by_user_id=self.role.user_id,
        )
        self.session.add(decision_row)

        now = datetime.now(UTC)
        finding.last_decision_at = now
        finding.closed_at = None
        if params.decision == SpmFindingDecisionType.DISMISS:
            finding.status = SpmFindingStatus.DISMISSED.value
            finding.closed_at = now
        elif params.decision == SpmFindingDecisionType.REOPEN:
            finding.status = SpmFindingStatus.OPEN.value
        elif params.decision == SpmFindingDecisionType.ENFORCE:
            finding.status = SpmFindingStatus.ENFORCEMENT_PENDING.value
            if finding.recommended_action is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Finding has no recommended action to enforce",
                )
            self.session.add(
                SpmEnforcementTask(
                    id=uuid.uuid4(),
                    organization_id=self.organization_id,
                    endpoint_id=finding.endpoint_id,
                    finding_id=finding.id,
                    action=finding.recommended_action,
                    payload=finding.recommended_payload or {},
                    status=SpmEnforcementTaskStatus.PENDING.value,
                    requested_by_user_id=self.role.user_id,
                )
            )

        await self.session.commit()
        await self.session.refresh(decision_row)
        return SpmFindingDecisionRead.model_validate(decision_row)

    async def _get_endpoint_row(self, endpoint_id: uuid.UUID) -> SpmEndpoint:
        stmt = select(SpmEndpoint).where(
            SpmEndpoint.id == endpoint_id,
            SpmEndpoint.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"SPM endpoint not found: {endpoint_id}")
        return row

    async def _get_finding_row(self, finding_id: uuid.UUID) -> SpmFinding:
        stmt = select(SpmFinding).where(
            SpmFinding.id == finding_id,
            SpmFinding.organization_id == self.organization_id,
        )
        row = (await self.session.scalars(stmt)).one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"SPM finding not found: {finding_id}")
        return row


class SpmSyncService:
    """Endpoint-authenticated SPM sync service."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def sync_endpoint(
        self,
        *,
        endpoint_id: uuid.UUID,
        bearer_token: str,
        params: SpmEndpointSyncRequest,
    ) -> SpmEndpointSyncResponse:
        endpoint = await self._authenticate_endpoint(
            endpoint_id=endpoint_id,
            bearer_token=bearer_token,
        )
        if not await is_org_entitled(
            self.session, endpoint.organization_id, Entitlement.SPM
        ):
            raise EntitlementRequired(Entitlement.SPM.value)

        issued_secret: str | None = None
        if endpoint.endpoint_secret_hash is None:
            issued_secret = _issue_secret("tcspm_ep")
            endpoint.endpoint_secret_hash = _hash_token(issued_secret)
            endpoint.enrollment_token_hash = None
            endpoint.enrolled_at = datetime.now(UTC)

        endpoint.status = params.status.value
        endpoint.name = params.name or endpoint.name
        endpoint.endpoint_version = params.endpoint_version or endpoint.endpoint_version
        endpoint.hostname = params.hostname or endpoint.hostname
        endpoint.os_user = params.os_user or endpoint.os_user
        endpoint.home_path = params.home_path or endpoint.home_path
        endpoint.client_metadata = params.client_metadata
        endpoint.last_seen_at = datetime.now(UTC)
        endpoint.last_sync_at = endpoint.last_seen_at
        endpoint.last_sync_error = None

        for asset in params.assets:
            await self._upsert_asset(endpoint=endpoint, asset=asset)
        for task_result in params.task_results:
            await self._apply_task_result(endpoint=endpoint, task_result=task_result)

        await self.session.commit()
        await self.session.refresh(endpoint)
        tasks = await self._pending_tasks(endpoint.id, endpoint.organization_id)
        return SpmEndpointSyncResponse(
            endpoint=SpmEndpointRead.model_validate(endpoint),
            endpoint_secret=issued_secret,
            tasks=[SpmEnforcementTaskRead.model_validate(task) for task in tasks],
        )

    async def _authenticate_endpoint(
        self,
        *,
        endpoint_id: uuid.UUID,
        bearer_token: str,
    ) -> SpmEndpoint:
        stmt = select(SpmEndpoint).where(SpmEndpoint.id == endpoint_id)
        endpoint = (await self.session.scalars(stmt)).one_or_none()
        if endpoint is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SPM endpoint not found: {endpoint_id}",
            )

        supplied_hash = _hash_token(bearer_token)
        expected_hash = endpoint.endpoint_secret_hash or endpoint.enrollment_token_hash
        if expected_hash is None or not secrets.compare_digest(
            supplied_hash, expected_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid endpoint token",
            )
        return endpoint

    async def _upsert_asset(
        self,
        *,
        endpoint: SpmEndpoint,
        asset: SpmSyncAssetUpsert,
    ) -> None:
        stmt = select(SpmAsset).where(
            SpmAsset.organization_id == endpoint.organization_id,
            SpmAsset.harness == asset.harness.value,
            SpmAsset.asset_class == asset.asset_class.value,
            SpmAsset.asset_type == asset.asset_type.value,
            SpmAsset.identity_key == asset.identity_key,
        )
        now = datetime.now(UTC)
        asset_row = (await self.session.scalars(stmt)).one_or_none()
        if asset_row is None:
            asset_row = SpmAsset(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                harness=asset.harness.value,
                asset_class=asset.asset_class.value,
                asset_type=asset.asset_type.value,
                identity_key=asset.identity_key,
                display_name=asset.display_name,
                content_hash=asset.content_hash,
                asset_metadata=asset.metadata,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(asset_row)
            await self.session.flush()
        else:
            asset_row.display_name = asset.display_name
            asset_row.content_hash = asset.content_hash
            asset_row.asset_metadata = asset.metadata
            asset_row.last_seen_at = now

        sighting_stmt = select(SpmAssetSighting).where(
            SpmAssetSighting.organization_id == endpoint.organization_id,
            SpmAssetSighting.endpoint_id == endpoint.id,
            SpmAssetSighting.asset_id == asset_row.id,
        )
        sighting = (await self.session.scalars(sighting_stmt)).one_or_none()
        if sighting is None:
            sighting = SpmAssetSighting(
                id=uuid.uuid4(),
                organization_id=endpoint.organization_id,
                endpoint_id=endpoint.id,
                asset_id=asset_row.id,
                workspace_id=asset.workspace_id,
                evidence=asset.evidence,
                observed_state=asset.observed_state,
                content_hash=asset.content_hash,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(sighting)
        else:
            sighting.workspace_id = asset.workspace_id
            sighting.evidence = asset.evidence
            sighting.observed_state = asset.observed_state
            sighting.content_hash = asset.content_hash
            sighting.last_seen_at = now

    async def _apply_task_result(
        self,
        *,
        endpoint: SpmEndpoint,
        task_result: SpmSyncTaskResult,
    ) -> None:
        stmt = select(SpmEnforcementTask).where(
            SpmEnforcementTask.id == task_result.task_id,
            SpmEnforcementTask.organization_id == endpoint.organization_id,
            SpmEnforcementTask.endpoint_id == endpoint.id,
        )
        task = (await self.session.scalars(stmt)).one_or_none()
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SPM enforcement task not found: {task_result.task_id}",
            )
        task.status = task_result.status.value
        task.result = task_result.result
        task.error = task_result.error
        task.completed_at = task_result.completed_at

        if task.finding_id is not None:
            finding_stmt = select(SpmFinding).where(
                SpmFinding.id == task.finding_id,
                SpmFinding.organization_id == endpoint.organization_id,
            )
            finding = (await self.session.scalars(finding_stmt)).one_or_none()
            if finding is not None:
                if task_result.status == SpmSyncTaskResultStatus.APPLIED:
                    finding.status = SpmFindingStatus.ENFORCED.value
                    finding.closed_at = task_result.completed_at
                elif task_result.status == SpmSyncTaskResultStatus.FAILED:
                    finding.status = SpmFindingStatus.OPEN.value
                    finding.closed_at = None

    async def _pending_tasks(
        self,
        endpoint_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> list[SpmEnforcementTask]:
        stmt = (
            select(SpmEnforcementTask)
            .where(
                SpmEnforcementTask.organization_id == organization_id,
                SpmEnforcementTask.endpoint_id == endpoint_id,
                SpmEnforcementTask.status == SpmEnforcementTaskStatus.PENDING.value,
            )
            .order_by(SpmEnforcementTask.created_at.asc(), SpmEnforcementTask.id.asc())
        )
        return list((await self.session.scalars(stmt)).all())
