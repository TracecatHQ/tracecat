"""Service-level tests for AI SPM read APIs."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.spm.analyzer import SpmInventoryAnalyzer
from tracecat_ee.spm.schemas import (
    SpmAssetQueryParams,
    SpmEndpointCreate,
    SpmEndpointSyncRequest,
    SpmFindingQueryParams,
    SpmSyncAssetUpsert,
)
from tracecat_ee.spm.service import SpmService, SpmSyncService
from tracecat_ee.spm.types import (
    SpmAssetClass,
    SpmAssetType,
    SpmEndpointPlatform,
    SpmHarness,
)

from tracecat.auth.types import Role
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


@pytest.mark.anyio
async def test_list_assets_and_endpoint_assets_preserve_endpoint_state(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        analyzer=SpmInventoryAnalyzer(
            session,
            schedule_background_tasks=False,
        ),
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
        analyzer=SpmInventoryAnalyzer(
            session,
            schedule_background_tasks=False,
        ),
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
    assert {finding.control_id for finding in control_findings.items} == {
        "claude.mcp_server.approved"
    }
