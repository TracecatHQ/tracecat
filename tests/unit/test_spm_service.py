"""Service-level tests for AI SPM read APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.spm.exceptions import SpmConflictError, SpmNotFoundError
from tracecat_ee.spm.intel import NoopSpmThreatIntelProvider
from tracecat_ee.spm.schemas import (
    SpmAssetQueryParams,
    SpmEndpointCreate,
    SpmEndpointSyncRequest,
    SpmFindingDecisionCreate,
    SpmFindingQueryParams,
    SpmSyncAssetUpsert,
    SpmSyncTaskResult,
)
from tracecat_ee.spm.service import SpmService, SpmSyncService
from tracecat_ee.spm.types import (
    SpmAssetClass,
    SpmAssetType,
    SpmEndpointPlatform,
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmHarness,
    SpmSyncTaskResultStatus,
)

from tracecat.auth.types import Role
from tracecat.db.models import (
    SpmAsset,
    SpmAssetSighting,
    SpmEndpoint,
    SpmEnforcementTask,
    SpmFinding,
    User,
)
from tracecat.pagination import CursorPaginationParams


async def _sync_assets(
    sync_service: SpmSyncService,
    *,
    endpoint_id: uuid.UUID,
    bearer_token: str,
    assets: list[SpmSyncAssetUpsert],
) -> None:
    await sync_service.sync_endpoint(
        endpoint_id=endpoint_id,
        bearer_token=bearer_token,
        params=SpmEndpointSyncRequest(
            name="Chris MacBook",
            assets=assets,
        ),
    )


def _mcp_asset(
    *,
    server_name: str,
    resolved_identity: str,
    disabled: bool = False,
) -> SpmSyncAssetUpsert:
    return SpmSyncAssetUpsert(
        harness=SpmHarness.CLAUDE_CODE,
        asset_class=SpmAssetClass.MCP_SERVER,
        asset_type=SpmAssetType.MCP_SERVER,
        identity_key=(
            f"file:/Users/chris/.claude.json#mcp:{server_name}|{resolved_identity}"
        ),
        display_name=server_name,
        metadata={
            "file_path": "/Users/chris/.claude.json",
            "source_surface": "user_state_json",
            "parse_status": "ok",
            "server_name": server_name,
            "resolved_identity": resolved_identity,
            "mcp_identity_key": f"{server_name}|{resolved_identity}",
        },
        evidence={"config": {"url": resolved_identity}},
        observed_state={"disabled": disabled},
    )


def _permission_asset() -> SpmSyncAssetUpsert:
    return SpmSyncAssetUpsert(
        harness=SpmHarness.CLAUDE_CODE,
        asset_class=SpmAssetClass.PERMISSIONS,
        asset_type=SpmAssetType.PERMISSION_CONFIG,
        identity_key="/Users/chris/.claude/settings.json#permission_config",
        display_name="permissions in settings.json",
        metadata={
            "file_path": "/Users/chris/.claude/settings.json",
            "parse_status": "ok",
            "project_root": "",
            "source_surface": "user_settings_json",
            "writable": True,
        },
        evidence={"permissions": {"defaultMode": "acceptEdits"}},
        observed_state={"default_mode": "acceptEdits"},
    )


async def _ensure_spm_user(session: AsyncSession, svc_role: Role) -> None:
    session.add(
        User(
            id=svc_role.user_id or uuid.uuid4(),
            email=f"spm-{uuid.uuid4()}@example.com",
            hashed_password="test_password",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            last_login_at=None,
        )
    )
    await session.commit()


async def _set_model_updated_at(
    session: AsyncSession,
    model: type[Any],
    ids: list[uuid.UUID],
    *,
    base_time: datetime,
) -> None:
    rows = list((await session.scalars(select(model).where(model.id.in_(ids)))).all())
    order = {row_id: idx for idx, row_id in enumerate(ids)}
    rows.sort(key=lambda row: order[row.id])
    for index, row in enumerate(rows):
        row.updated_at = base_time + timedelta(minutes=index)
    await session.commit()


@pytest.mark.anyio
async def test_list_assets_and_endpoint_assets_preserve_endpoint_state(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )
    endpoint_one = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )
    endpoint_two = await service.create_endpoint(
        SpmEndpointCreate(
            name="CI Mac Mini",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    github_asset = SpmSyncAssetUpsert(
        harness=SpmHarness.CLAUDE_CODE,
        asset_class=SpmAssetClass.MCP_SERVER,
        asset_type=SpmAssetType.MCP_SERVER,
        identity_key="file:/Users/chris/.claude.json#mcp:github|https://api.github.com/mcp",
        display_name="github",
        metadata={
            "file_path": "/Users/chris/.claude.json",
            "server_name": "github",
            "resolved_identity": "https://api.github.com/mcp",
        },
        evidence={"config": {"url": "https://api.github.com/mcp"}},
        observed_state={"disabled": False},
    )
    disabled_github_asset = github_asset.model_copy(
        update={"observed_state": {"disabled": True}}
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await _sync_assets(
            sync_service,
            endpoint_id=endpoint_one.endpoint.id,
            bearer_token=endpoint_one.enrollment_token,
            assets=[github_asset],
        )
        await _sync_assets(
            sync_service,
            endpoint_id=endpoint_two.endpoint.id,
            bearer_token=endpoint_two.enrollment_token,
            assets=[disabled_github_asset],
        )

    deduped_assets = await service.list_assets(
        SpmAssetQueryParams(
            limit=50,
            endpoint_id=endpoint_one.endpoint.id,
            harness=SpmHarness.CLAUDE_CODE,
            asset_class=SpmAssetClass.MCP_SERVER,
            asset_type=SpmAssetType.MCP_SERVER,
        )
    )
    endpoint_one_assets = await service.list_endpoint_assets(
        endpoint_one.endpoint.id,
        CursorPaginationParams(limit=50),
    )
    endpoint_two_assets = await service.list_endpoint_assets(
        endpoint_two.endpoint.id,
        CursorPaginationParams(limit=50),
    )

    assert len(deduped_assets.items) == 1
    assert endpoint_one_assets.items[0].asset_id == deduped_assets.items[0].id
    assert endpoint_one_assets.items[0].observed_state == {"disabled": False}
    assert endpoint_two_assets.items[0].asset_id == deduped_assets.items[0].id
    assert endpoint_two_assets.items[0].observed_state == {"disabled": True}


@pytest.mark.anyio
async def test_list_findings_supports_endpoint_and_control_filters(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )
    endpoint_one = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )
    endpoint_two = await service.create_endpoint(
        SpmEndpointCreate(
            name="CI Mac Mini",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await _sync_assets(
            sync_service,
            endpoint_id=endpoint_one.endpoint.id,
            bearer_token=endpoint_one.enrollment_token,
            assets=[
                SpmSyncAssetUpsert(
                    harness=SpmHarness.CLAUDE_CODE,
                    asset_class=SpmAssetClass.MCP_SERVER,
                    asset_type=SpmAssetType.MCP_SERVER,
                    identity_key="file:/Users/chris/.claude.json#mcp:github|https://api.github.com/mcp",
                    display_name="github",
                    metadata={
                        "file_path": "/Users/chris/.claude.json",
                        "server_name": "github",
                        "resolved_identity": "https://api.github.com/mcp",
                    },
                    evidence={"config": {"url": "https://api.github.com/mcp"}},
                    observed_state={"disabled": False},
                )
            ],
        )
        await _sync_assets(
            sync_service,
            endpoint_id=endpoint_two.endpoint.id,
            bearer_token=endpoint_two.enrollment_token,
            assets=[
                SpmSyncAssetUpsert(
                    harness=SpmHarness.CLAUDE_CODE,
                    asset_class=SpmAssetClass.INSTRUCTION_FILE,
                    asset_type=SpmAssetType.CLAUDE_MD,
                    identity_key="/Users/chris/project/CLAUDE.md",
                    display_name="CLAUDE.md",
                    metadata={
                        "file_path": "/Users/chris/project/CLAUDE.md",
                        "project_root": "/Users/chris/project",
                        "source_surface": "project_claude_md",
                        "parse_status": "ok",
                    },
                    evidence={
                        "language_signal": {"likely_english": False},
                        "obfuscation": {"obfuscation_detected": True},
                        "urls": [],
                        "domains": [],
                        "ips": [],
                        "indicator_reputation_status": "good",
                    },
                    observed_state={"excluded": False},
                )
            ],
        )

    endpoint_one_findings = await service.list_findings(
        SpmFindingQueryParams(limit=50, endpoint_id=endpoint_one.endpoint.id)
    )
    control_findings = await service.list_findings(
        SpmFindingQueryParams(limit=50, control_id="claude.mcp_server.approved")
    )

    assert {finding.endpoint_id for finding in endpoint_one_findings.items} == {
        endpoint_one.endpoint.id
    }
    assert {finding.control_key for finding in control_findings.items} == {
        "claude.mcp_server.approved"
    }


@pytest.mark.anyio
async def test_spm_list_methods_paginate_without_duplicates(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )

    created_endpoints = []
    for name in ("alpha", "beta", "gamma"):
        created_endpoints.append(
            await service.create_endpoint(
                SpmEndpointCreate(
                    name=name,
                    harness=SpmHarness.CLAUDE_CODE,
                    platform=SpmEndpointPlatform.MACOS,
                )
            )
        )

    base_time = datetime(2026, 4, 22, tzinfo=UTC)
    await _set_model_updated_at(
        session,
        SpmEndpoint,
        [created.endpoint.id for created in created_endpoints],
        base_time=base_time,
    )

    endpoints_page_one = await service.list_endpoints(CursorPaginationParams(limit=2))
    endpoints_page_two = await service.list_endpoints(
        CursorPaginationParams(limit=2, cursor=endpoints_page_one.next_cursor)
    )

    assert [endpoint.name for endpoint in endpoints_page_one.items] == ["gamma", "beta"]
    assert [endpoint.name for endpoint in endpoints_page_two.items] == ["alpha"]
    assert endpoints_page_one.has_more is True
    assert endpoints_page_two.has_more is False

    primary_endpoint = created_endpoints[0]
    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        response = await sync_service.sync_endpoint(
            endpoint_id=primary_endpoint.endpoint.id,
            bearer_token=primary_endpoint.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="alpha",
                client_metadata={
                    "spm_policy": {
                        "approved_mcp_servers": [
                            {
                                "server_name": "approved",
                                "resolved_identity": "https://allowed.example/mcp",
                            }
                        ]
                    }
                },
                assets=[
                    _mcp_asset(
                        server_name="gamma",
                        resolved_identity="https://gamma.example/mcp",
                    ),
                    _mcp_asset(
                        server_name="beta",
                        resolved_identity="https://beta.example/mcp",
                    ),
                    _mcp_asset(
                        server_name="alpha",
                        resolved_identity="https://alpha.example/mcp",
                    ),
                ],
            ),
        )

    asset_rows = list((await session.scalars(select(SpmAsset))).all())
    asset_ids_by_name = {asset.display_name: asset.id for asset in asset_rows}
    finding_rows = list((await session.scalars(select(SpmFinding))).all())
    finding_ids_by_asset_name = {
        next(
            asset.display_name for asset in asset_rows if asset.id == finding.asset_id
        ): finding.id
        for finding in finding_rows
        if finding.control_key == "claude.mcp_server.approved"
    }
    await _set_model_updated_at(
        session,
        SpmAsset,
        [
            asset_ids_by_name["alpha"],
            asset_ids_by_name["beta"],
            asset_ids_by_name["gamma"],
        ],
        base_time=base_time,
    )
    await _set_model_updated_at(
        session,
        SpmFinding,
        [
            finding_ids_by_asset_name["alpha"],
            finding_ids_by_asset_name["beta"],
            finding_ids_by_asset_name["gamma"],
        ],
        base_time=base_time + timedelta(hours=1),
    )

    assets_page_one = await service.list_assets(
        SpmAssetQueryParams(
            limit=2,
            endpoint_id=primary_endpoint.endpoint.id,
            harness=SpmHarness.CLAUDE_CODE,
            asset_class=SpmAssetClass.MCP_SERVER,
            asset_type=SpmAssetType.MCP_SERVER,
        )
    )
    assets_page_two = await service.list_assets(
        SpmAssetQueryParams(
            limit=2,
            cursor=assets_page_one.next_cursor,
            endpoint_id=primary_endpoint.endpoint.id,
            harness=SpmHarness.CLAUDE_CODE,
            asset_class=SpmAssetClass.MCP_SERVER,
            asset_type=SpmAssetType.MCP_SERVER,
        )
    )
    findings_page_one = await service.list_findings(SpmFindingQueryParams(limit=2))
    findings_page_two = await service.list_findings(
        SpmFindingQueryParams(limit=2, cursor=findings_page_one.next_cursor)
    )

    assert [asset.display_name for asset in assets_page_one.items] == ["gamma", "beta"]
    assert [asset.display_name for asset in assets_page_two.items] == ["alpha"]
    assert assets_page_one.has_more is True
    assert assets_page_two.has_more is False
    assert [finding.summary for finding in findings_page_one.items] == [
        "gamma is not approved",
        "beta is not approved",
    ]
    assert [finding.summary for finding in findings_page_two.items] == [
        "alpha is not approved"
    ]
    assert findings_page_one.has_more is True
    assert findings_page_two.has_more is False
    assert response.endpoint_secret is not None


@pytest.mark.anyio
async def test_delete_pending_endpoint_removes_unused_enrollment(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )
    created = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    await service.delete_pending_endpoint(created.endpoint.id)

    endpoints = await service.list_endpoints(CursorPaginationParams(limit=50))
    assert endpoints.items == []

    with pytest.raises(SpmNotFoundError) as read_exc:
        await service.get_endpoint(created.endpoint.id)

    assert read_exc.value.code == "spm_endpoint_not_found"

    with pytest.raises(SpmNotFoundError) as sync_exc:
        await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            params=SpmEndpointSyncRequest(name="Chris MacBook"),
        )

    assert sync_exc.value.code == "spm_endpoint_not_found"


@pytest.mark.anyio
async def test_delete_pending_endpoint_rejects_active_endpoint(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )
    created = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await _sync_assets(
            sync_service,
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            assets=[],
        )

    with pytest.raises(SpmConflictError) as exc_info:
        await service.delete_pending_endpoint(created.endpoint.id)

    assert exc_info.value.code == "spm_endpoint_delete_conflict"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("task_status", "asset_disabled", "expected_finding_status"),
    [
        (
            SpmSyncTaskResultStatus.APPLIED,
            True,
            SpmFindingStatus.RESOLVED,
        ),
        (
            SpmSyncTaskResultStatus.FAILED,
            False,
            SpmFindingStatus.OPEN,
        ),
    ],
)
async def test_sync_endpoint_task_results_reconcile_task_and_finding_state(
    session: AsyncSession,
    svc_role: Role,
    task_status: SpmSyncTaskResultStatus,
    asset_disabled: bool,
    expected_finding_status: SpmFindingStatus,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )
    created = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        first_response = await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Chris MacBook",
                client_metadata={
                    "spm_policy": {
                        "approved_mcp_servers": [
                            {
                                "server_name": "approved",
                                "resolved_identity": "https://allowed.example/mcp",
                            }
                        ]
                    }
                },
                assets=[
                    _mcp_asset(
                        server_name="github",
                        resolved_identity="https://api.github.com/mcp",
                    )
                ],
            ),
        )

    finding = (
        await session.scalars(
            select(SpmFinding).where(
                SpmFinding.endpoint_id == created.endpoint.id,
                SpmFinding.control_key == "claude.mcp_server.approved",
            )
        )
    ).one()

    await _ensure_spm_user(session, svc_role)
    await service.create_finding_decision(
        finding.id,
        SpmFindingDecisionCreate(decision=SpmFindingDecisionType.ENFORCE),
    )

    task = (
        await session.scalars(
            select(SpmEnforcementTask).where(
                SpmEnforcementTask.finding_id == finding.id,
                SpmEnforcementTask.status == SpmEnforcementTaskStatus.PENDING.value,
            )
        )
    ).one()

    approved_identity = (
        "https://api.github.com/mcp"
        if task_status == SpmSyncTaskResultStatus.APPLIED
        else "https://allowed.example/mcp"
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=first_response.endpoint_secret or created.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Chris MacBook",
                client_metadata={
                    "spm_policy": {
                        "approved_mcp_servers": [
                            {
                                "server_name": "github",
                                "resolved_identity": approved_identity,
                            }
                        ]
                    }
                },
                assets=[
                    _mcp_asset(
                        server_name="github",
                        resolved_identity="https://api.github.com/mcp",
                        disabled=asset_disabled,
                    )
                ],
                task_results=[
                    SpmSyncTaskResult(
                        task_id=task.id,
                        status=task_status,
                        result={"target_path": "/Users/chris/.claude.json"},
                        error="local apply failed"
                        if task_status == SpmSyncTaskResultStatus.FAILED
                        else None,
                    )
                ],
            ),
        )

    await session.refresh(task)
    await session.refresh(finding)

    assert task.status == task_status.value
    assert task.completed_at is not None
    assert task.result["target_path"] == "/Users/chris/.claude.json"
    if task_status == SpmSyncTaskResultStatus.FAILED:
        assert task.error == "local apply failed"
    else:
        assert task.error is None
    assert finding.status == expected_finding_status.value
    if expected_finding_status == SpmFindingStatus.OPEN:
        assert finding.closed_at is None
    else:
        assert finding.closed_at is not None


@pytest.mark.anyio
async def test_upsert_asset_recovers_from_duplicate_insert_race() -> None:
    class _ScalarResult:
        def __init__(self, value: Any) -> None:
            self.value = value

        def one_or_none(self) -> Any:
            return self.value

        def one(self) -> Any:
            if self.value is None:
                raise AssertionError("Expected row to exist")
            return self.value

    class _NestedTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _SessionStub:
        def __init__(self, asset_row: SpmAsset) -> None:
            self.asset_row = asset_row
            self.scalars_calls = 0
            self.flush_calls = 0
            self.added: list[Any] = []

        async def scalars(self, _stmt: Any) -> _ScalarResult:
            self.scalars_calls += 1
            if self.scalars_calls == 1:
                return _ScalarResult(None)
            if self.scalars_calls == 2:
                return _ScalarResult(self.asset_row)
            return _ScalarResult(None)

        def add(self, row: Any) -> None:
            self.added.append(row)

        async def flush(self) -> None:
            self.flush_calls += 1
            if self.flush_calls == 1:
                raise IntegrityError(
                    "INSERT INTO spm_asset ...",
                    params={},
                    orig=Exception("duplicate key value violates unique constraint"),
                )

        def begin_nested(self) -> _NestedTransaction:
            return _NestedTransaction()

    existing_asset = SpmAsset(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        harness=SpmHarness.CLAUDE_CODE.value,
        asset_class=SpmAssetClass.PERMISSIONS.value,
        asset_type=SpmAssetType.PERMISSION_CONFIG.value,
        identity_key="/Users/chris/.claude/settings.json#permission_config",
        display_name="stale name",
        content_hash=None,
        asset_metadata={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    session = _SessionStub(existing_asset)
    service = SpmSyncService(session=session)  # type: ignore[arg-type]

    endpoint = SpmEndpoint(
        id=uuid.uuid4(),
        organization_id=existing_asset.organization_id,
        name="Pending MacBook",
        harness=SpmHarness.CLAUDE_CODE.value,
        platform=SpmEndpointPlatform.MACOS.value,
        status="pending",
    )

    await service._upsert_asset(endpoint=endpoint, asset=_permission_asset())

    assert session.flush_calls == 1
    assert existing_asset.display_name == "permissions in settings.json"
    assert existing_asset.asset_metadata["source_surface"] == "user_settings_json"
    sighting = next(row for row in session.added if isinstance(row, SpmAssetSighting))
    assert sighting.asset_id == existing_asset.id
