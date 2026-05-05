"""Service-level tests for AI SPM read APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.spm.exceptions import SpmConflictError, SpmNotFoundError
from tracecat_ee.spm.intel import NoopSpmThreatIntelProvider
from tracecat_ee.spm.schemas import (
    SpmEndpointCreate,
    SpmEndpointSyncRequest,
    SpmFindingDecisionCreate,
    SpmFindingQueryParams,
    SpmInventoryQueryParams,
    SpmResponseActionPreviewCreate,
    SpmSyncInventoryItemUpsert,
    SpmSyncInventoryRelationshipUpsert,
    SpmSyncResponseActionPreviewResult,
    SpmSyncTaskResult,
)
from tracecat_ee.spm.service import SpmService, SpmSyncService
from tracecat_ee.spm.types import (
    SpmEndpointComplianceStatus,
    SpmEndpointPlatform,
    SpmEnforcementTaskStatus,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmHarness,
    SpmInventoryItemType,
    SpmInventoryRelationshipType,
    SpmInventorySourceType,
    SpmResponseActionPreviewStatus,
    SpmSyncTaskResultStatus,
)

from tracecat.auth.types import Role
from tracecat.db.models import (
    SpmEndpoint,
    SpmEnforcementTask,
    SpmFinding,
    SpmInventoryItem,
    SpmInventoryObservation,
    SpmInventoryRelationship,
    SpmResponseActionPreview,
    User,
)
from tracecat.pagination import CursorPaginationParams


async def _sync_inventory_items(
    sync_service: SpmSyncService,
    *,
    endpoint_id: uuid.UUID,
    bearer_token: str,
    items: list[SpmSyncInventoryItemUpsert],
) -> None:
    await sync_service.sync_endpoint(
        endpoint_id=endpoint_id,
        bearer_token=bearer_token,
        params=SpmEndpointSyncRequest(
            name="Chris MacBook",
            inventory_items=items,
        ),
    )


def _mcp_item(
    *,
    server_name: str,
    resolved_identity: str,
    disabled: bool = False,
) -> SpmSyncInventoryItemUpsert:
    return SpmSyncInventoryItemUpsert(
        harness=SpmHarness.CLAUDE_CODE,
        item_type=SpmInventoryItemType.MCP_SERVER,
        source_type=SpmInventorySourceType.CLAUDE_JSON,
        item_location=f"{server_name}|{resolved_identity}",
        source_location="/Users/chris/.claude.json",
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


def _permission_item() -> SpmSyncInventoryItemUpsert:
    return SpmSyncInventoryItemUpsert(
        harness=SpmHarness.CLAUDE_CODE,
        item_type=SpmInventoryItemType.PERMISSION_CONFIG,
        source_type=SpmInventorySourceType.SETTINGS_JSON,
        item_location="/Users/chris/.claude/settings.json",
        source_location="/Users/chris/.claude/settings.json",
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
async def test_list_inventory_and_endpoint_inventory_preserve_endpoint_state(
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

    github_item = SpmSyncInventoryItemUpsert(
        harness=SpmHarness.CLAUDE_CODE,
        item_type=SpmInventoryItemType.MCP_SERVER,
        source_type=SpmInventorySourceType.CLAUDE_JSON,
        item_location="github|https://api.github.com/mcp",
        source_location="/Users/chris/.claude.json",
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
    disabled_github_item = github_item.model_copy(
        update={"observed_state": {"disabled": True}}
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await _sync_inventory_items(
            sync_service,
            endpoint_id=endpoint_one.endpoint.id,
            bearer_token=endpoint_one.enrollment_token,
            items=[github_item],
        )
        await _sync_inventory_items(
            sync_service,
            endpoint_id=endpoint_two.endpoint.id,
            bearer_token=endpoint_two.enrollment_token,
            items=[disabled_github_item],
        )

    deduped_items = await service.list_inventory(
        SpmInventoryQueryParams(
            limit=50,
            endpoint_id=endpoint_one.endpoint.id,
            harness=SpmHarness.CLAUDE_CODE,
            item_type=SpmInventoryItemType.MCP_SERVER,
            source_type=SpmInventorySourceType.CLAUDE_JSON,
        )
    )
    endpoint_one_items = await service.list_endpoint_inventory(
        endpoint_one.endpoint.id,
        CursorPaginationParams(limit=50),
    )
    endpoint_two_items = await service.list_endpoint_inventory(
        endpoint_two.endpoint.id,
        CursorPaginationParams(limit=50),
    )

    assert len(deduped_items.items) == 1
    assert endpoint_one_items.items[0].inventory_item_id == deduped_items.items[0].id
    assert endpoint_one_items.items[0].observed_state == {"disabled": False}
    assert endpoint_two_items.items[0].inventory_item_id == deduped_items.items[0].id
    assert endpoint_two_items.items[0].observed_state == {"disabled": True}


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
        await _sync_inventory_items(
            sync_service,
            endpoint_id=endpoint_one.endpoint.id,
            bearer_token=endpoint_one.enrollment_token,
            items=[
                SpmSyncInventoryItemUpsert(
                    harness=SpmHarness.CLAUDE_CODE,
                    item_type=SpmInventoryItemType.MCP_SERVER,
                    source_type=SpmInventorySourceType.CLAUDE_JSON,
                    item_location="github|https://api.github.com/mcp",
                    source_location="/Users/chris/.claude.json",
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
        await _sync_inventory_items(
            sync_service,
            endpoint_id=endpoint_two.endpoint.id,
            bearer_token=endpoint_two.enrollment_token,
            items=[
                SpmSyncInventoryItemUpsert(
                    harness=SpmHarness.CLAUDE_CODE,
                    item_type=SpmInventoryItemType.INSTRUCTION_FILE,
                    source_type=SpmInventorySourceType.CLAUDE_MD,
                    item_location="/Users/chris/project/CLAUDE.md",
                    source_location="/Users/chris/project/CLAUDE.md",
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
async def test_endpoint_compliance_statuses_are_computed_from_inventory_and_findings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    sync_service = SpmSyncService(
        session,
        threat_intel_provider=NoopSpmThreatIntelProvider(),
    )
    pending = await service.create_endpoint(
        SpmEndpointCreate(
            name="Pending MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )
    clean = await service.create_endpoint(
        SpmEndpointCreate(
            name="Clean MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )
    attention = await service.create_endpoint(
        SpmEndpointCreate(
            name="Attention MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        clean_response = await sync_service.sync_endpoint(
            endpoint_id=clean.endpoint.id,
            bearer_token=clean.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Clean MacBook",
                inventory_items=[
                    SpmSyncInventoryItemUpsert(
                        harness=SpmHarness.CLAUDE_CODE,
                        item_type=SpmInventoryItemType.PLUGIN,
                        source_type=SpmInventorySourceType.PLUGIN_MANIFEST,
                        item_location="/Users/chris/.claude/plugins/demo/.claude-plugin/plugin.json",
                        source_location="/Users/chris/.claude/plugins/demo/.claude-plugin/plugin.json",
                        identity_key="/Users/chris/.claude/plugins/demo/.claude-plugin/plugin.json",
                        display_name="demo",
                        metadata={"parse_status": "ok"},
                        observed_state={"enabled": True},
                    )
                ],
            ),
        )
        attention_response = await sync_service.sync_endpoint(
            endpoint_id=attention.endpoint.id,
            bearer_token=attention.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Attention MacBook",
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
                inventory_items=[
                    _mcp_item(
                        server_name="github",
                        resolved_identity="https://api.github.com/mcp",
                    )
                ],
            ),
        )

    assert (
        clean_response.endpoint.compliance_status
        == SpmEndpointComplianceStatus.COMPLIANT
    )
    assert (
        attention_response.endpoint.compliance_status
        == SpmEndpointComplianceStatus.NEEDS_ATTENTION
    )

    endpoints_by_id = {
        endpoint.id: endpoint
        for endpoint in (
            await service.list_endpoints(CursorPaginationParams(limit=50))
        ).items
    }
    assert (
        endpoints_by_id[pending.endpoint.id].compliance_status
        == SpmEndpointComplianceStatus.NOT_ASSESSED
    )
    assert (
        endpoints_by_id[clean.endpoint.id].compliance_status
        == SpmEndpointComplianceStatus.COMPLIANT
    )
    assert (
        endpoints_by_id[attention.endpoint.id].compliance_status
        == SpmEndpointComplianceStatus.NEEDS_ATTENTION
    )

    finding = (
        await session.scalars(
            select(SpmFinding).where(
                SpmFinding.endpoint_id == attention.endpoint.id,
                SpmFinding.control_key == "claude.mcp_server.approved",
            )
        )
    ).one()
    await _ensure_spm_user(session, svc_role)
    await service.create_finding_decision(
        finding.id,
        SpmFindingDecisionCreate(decision=SpmFindingDecisionType.ENFORCE),
    )

    attention_read = await service.get_endpoint(attention.endpoint.id)
    assert (
        attention_read.compliance_status
        == SpmEndpointComplianceStatus.ENFORCEMENT_QUEUED
    )


@pytest.mark.anyio
async def test_sync_endpoint_persists_inventory_relationships(
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
            name="Plugin MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )

    plugin_identity = "/Users/chris/.claude/plugins/demo/.claude-plugin/plugin.json"
    skill_identity = "/Users/chris/.claude/plugins/demo/skills/review/SKILL.md"
    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Plugin MacBook",
                inventory_items=[
                    SpmSyncInventoryItemUpsert(
                        harness=SpmHarness.CLAUDE_CODE,
                        item_type=SpmInventoryItemType.PLUGIN,
                        source_type=SpmInventorySourceType.PLUGIN_MANIFEST,
                        item_location=plugin_identity,
                        source_location=plugin_identity,
                        identity_key=plugin_identity,
                        display_name="demo",
                        metadata={"parse_status": "ok"},
                        observed_state={"enabled": True},
                    ),
                    SpmSyncInventoryItemUpsert(
                        harness=SpmHarness.CLAUDE_CODE,
                        item_type=SpmInventoryItemType.SKILL,
                        source_type=SpmInventorySourceType.SKILL_FRONTMATTER,
                        item_location=skill_identity,
                        source_location=skill_identity,
                        identity_key=skill_identity,
                        display_name="review",
                        metadata={"parse_status": "ok", "name": "review"},
                        observed_state={"disabled": False},
                    ),
                ],
                relationships=[
                    SpmSyncInventoryRelationshipUpsert(
                        relationship_type=SpmInventoryRelationshipType.DEFINES,
                        from_identity_key=plugin_identity,
                        to_identity_key=skill_identity,
                        evidence={"source_location": skill_identity},
                        observed_state={"enabled": True},
                    )
                ],
            ),
        )

    relationship = (await session.scalars(select(SpmInventoryRelationship))).one()
    items = {
        item.identity_key: item
        for item in (await session.scalars(select(SpmInventoryItem))).all()
    }
    assert relationship.endpoint_id == created.endpoint.id
    assert relationship.relationship_type == SpmInventoryRelationshipType.DEFINES.value
    assert relationship.from_inventory_item_id == items[plugin_identity].id
    assert relationship.to_inventory_item_id == items[skill_identity].id


def test_sync_inventory_relationship_rejects_legacy_relationship_types() -> None:
    with pytest.raises(ValidationError):
        SpmSyncInventoryRelationshipUpsert.model_validate(
            {
                "relationship_type": "contains",
                "from_identity_key": "plugin",
                "to_identity_key": "skill",
            }
        )


def test_sync_inventory_item_rejects_invalid_taxonomy_binding() -> None:
    with pytest.raises(ValidationError):
        SpmSyncInventoryItemUpsert(
            harness=SpmHarness.CLAUDE_CODE,
            item_type=SpmInventoryItemType.PLUGIN,
            source_type=SpmInventorySourceType.CLAUDE_JSON,
            item_location="/Users/chris/.claude.json",
            source_location="/Users/chris/.claude.json",
            identity_key="/Users/chris/.claude.json#plugin",
            display_name="invalid plugin",
        )


def test_sync_inventory_item_rejects_invalid_source_location() -> None:
    with pytest.raises(ValidationError):
        SpmSyncInventoryItemUpsert(
            harness=SpmHarness.CLAUDE_CODE,
            item_type=SpmInventoryItemType.MCP_SERVER,
            source_type=SpmInventorySourceType.CLAUDE_JSON,
            item_location="github|https://api.githubcopilot.com/mcp/",
            source_location="/Users/chris/.claude/settings.json",
            identity_key=(
                "file:/Users/chris/.claude/settings.json"
                "#mcp:github|https://api.githubcopilot.com/mcp/"
            ),
            display_name="invalid source location",
        )


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
                inventory_items=[
                    _mcp_item(
                        server_name="gamma",
                        resolved_identity="https://gamma.example/mcp",
                    ),
                    _mcp_item(
                        server_name="beta",
                        resolved_identity="https://beta.example/mcp",
                    ),
                    _mcp_item(
                        server_name="alpha",
                        resolved_identity="https://alpha.example/mcp",
                    ),
                ],
            ),
        )

    item_rows = list((await session.scalars(select(SpmInventoryItem))).all())
    inventory_item_ids_by_name = {item.display_name: item.id for item in item_rows}
    finding_rows = list((await session.scalars(select(SpmFinding))).all())
    finding_ids_by_item_name = {
        next(
            item.display_name
            for item in item_rows
            if item.id == finding.inventory_item_id
        ): finding.id
        for finding in finding_rows
        if finding.control_key == "claude.mcp_server.approved"
    }
    await _set_model_updated_at(
        session,
        SpmInventoryItem,
        [
            inventory_item_ids_by_name["alpha"],
            inventory_item_ids_by_name["beta"],
            inventory_item_ids_by_name["gamma"],
        ],
        base_time=base_time,
    )
    await _set_model_updated_at(
        session,
        SpmFinding,
        [
            finding_ids_by_item_name["alpha"],
            finding_ids_by_item_name["beta"],
            finding_ids_by_item_name["gamma"],
        ],
        base_time=base_time + timedelta(hours=1),
    )

    items_page_one = await service.list_inventory(
        SpmInventoryQueryParams(
            limit=2,
            endpoint_id=primary_endpoint.endpoint.id,
            harness=SpmHarness.CLAUDE_CODE,
            item_type=SpmInventoryItemType.MCP_SERVER,
            source_type=SpmInventorySourceType.CLAUDE_JSON,
        )
    )
    items_page_two = await service.list_inventory(
        SpmInventoryQueryParams(
            limit=2,
            cursor=items_page_one.next_cursor,
            endpoint_id=primary_endpoint.endpoint.id,
            harness=SpmHarness.CLAUDE_CODE,
            item_type=SpmInventoryItemType.MCP_SERVER,
            source_type=SpmInventorySourceType.CLAUDE_JSON,
        )
    )
    findings_page_one = await service.list_findings(SpmFindingQueryParams(limit=2))
    findings_page_two = await service.list_findings(
        SpmFindingQueryParams(limit=2, cursor=findings_page_one.next_cursor)
    )

    assert [item.display_name for item in items_page_one.items] == ["gamma", "beta"]
    assert [item.display_name for item in items_page_two.items] == ["alpha"]
    assert items_page_one.has_more is True
    assert items_page_two.has_more is False
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
        await _sync_inventory_items(
            sync_service,
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            items=[],
        )

    with pytest.raises(SpmConflictError) as exc_info:
        await service.delete_pending_endpoint(created.endpoint.id)

    assert exc_info.value.code == "spm_endpoint_delete_conflict"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("task_status", "item_disabled", "expected_finding_status"),
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
    item_disabled: bool,
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
                inventory_items=[
                    _mcp_item(
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
                inventory_items=[
                    _mcp_item(
                        server_name="github",
                        resolved_identity="https://api.github.com/mcp",
                        disabled=item_disabled,
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
async def test_response_action_preview_sync_lifecycle(
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
                inventory_items=[
                    _mcp_item(
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
    preview = await service.create_response_action_preview(
        finding.id,
        SpmResponseActionPreviewCreate(),
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        sync_with_preview = await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=first_response.endpoint_secret or created.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Chris MacBook",
                inventory_items=[
                    _mcp_item(
                        server_name="github",
                        resolved_identity="https://api.github.com/mcp",
                    )
                ],
            ),
        )

    assert [item.id for item in sync_with_preview.action_previews] == [preview.id]

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=first_response.endpoint_secret or created.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Chris MacBook",
                inventory_items=[
                    _mcp_item(
                        server_name="github",
                        resolved_identity="https://api.github.com/mcp",
                    )
                ],
                action_preview_results=[
                    SpmSyncResponseActionPreviewResult(
                        preview_id=preview.id,
                        status=SpmResponseActionPreviewStatus.READY,
                        target_path="/Users/chris/.claude.json",
                        before_content="{}\n",
                        after_content='{"mcpServers":[]}\n',
                        result={"task_status": "applied"},
                    )
                ],
            ),
        )

    row = (
        await session.scalars(
            select(SpmResponseActionPreview).where(
                SpmResponseActionPreview.id == preview.id
            )
        )
    ).one()
    assert row.status == SpmResponseActionPreviewStatus.READY.value
    assert row.target_path == "/Users/chris/.claude.json"
    assert row.before_content == "{}\n"
    assert row.after_content == '{"mcpServers":[]}\n'


@pytest.mark.anyio
async def test_upsert_inventory_item_recovers_from_duplicate_insert_race() -> None:
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
        def __init__(self, item_row: SpmInventoryItem) -> None:
            self.item_row = item_row
            self.scalars_calls = 0
            self.flush_calls = 0
            self.added: list[Any] = []

        async def scalars(self, _stmt: Any) -> _ScalarResult:
            self.scalars_calls += 1
            if self.scalars_calls == 1:
                return _ScalarResult(None)
            if self.scalars_calls == 2:
                return _ScalarResult(self.item_row)
            return _ScalarResult(None)

        def add(self, row: Any) -> None:
            self.added.append(row)

        async def flush(self) -> None:
            self.flush_calls += 1
            if self.flush_calls == 1:
                raise IntegrityError(
                    "INSERT INTO spm_inventory_item ...",
                    params={},
                    orig=Exception("duplicate key value violates unique constraint"),
                )

        def begin_nested(self) -> _NestedTransaction:
            return _NestedTransaction()

    existing_item = SpmInventoryItem(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        harness=SpmHarness.CLAUDE_CODE.value,
        item_type=SpmInventoryItemType.PERMISSION_CONFIG.value,
        source_type=SpmInventorySourceType.SETTINGS_JSON.value,
        item_location="/Users/chris/.claude/settings.json",
        source_location="/Users/chris/.claude/settings.json",
        identity_key="/Users/chris/.claude/settings.json#permission_config",
        display_name="stale name",
        content_hash=None,
        item_metadata={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    session = _SessionStub(existing_item)
    service = SpmSyncService(session=session)  # type: ignore[arg-type]

    endpoint = SpmEndpoint(
        id=uuid.uuid4(),
        organization_id=existing_item.organization_id,
        name="Pending MacBook",
        harness=SpmHarness.CLAUDE_CODE.value,
        platform=SpmEndpointPlatform.MACOS.value,
        status="pending",
    )

    await service._upsert_inventory_item(endpoint=endpoint, item=_permission_item())

    assert session.flush_calls == 1
    assert existing_item.display_name == "permissions in settings.json"
    assert existing_item.item_metadata["source_surface"] == "user_settings_json"
    observation = next(
        row for row in session.added if isinstance(row, SpmInventoryObservation)
    )
    assert observation.inventory_item_id == existing_item.id
