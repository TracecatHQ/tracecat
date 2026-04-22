"""Analyzer and sync-flow tests for AI SPM."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.spm.analyzer import SpmInventoryAnalyzer
from tracecat_ee.spm.schemas import (
    SpmEndpointCreate,
    SpmEndpointSyncRequest,
    SpmFindingDecisionCreate,
    SpmSyncAssetUpsert,
)
from tracecat_ee.spm.service import SpmService, SpmSyncService
from tracecat_ee.spm.types import (
    SpmAssetClass,
    SpmAssetType,
    SpmEndpointPlatform,
    SpmEnforcementAction,
    SpmFindingDecisionType,
    SpmFindingStatus,
    SpmHarness,
)

from tracecat.auth.types import Role
from tracecat.db.models import SpmEnforcementTask, SpmFinding, User


class RecordingEnricher:
    """Capture requested external enrichment batches."""

    def __init__(self) -> None:
        self.calls: list[list[uuid.UUID]] = []

    async def enrich_findings(self, finding_ids: list[uuid.UUID]) -> None:
        self.calls.append(finding_ids)


@pytest.mark.anyio
async def test_sync_endpoint_creates_identity_based_mcp_findings_and_tasks(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    created = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )
    enricher = RecordingEnricher()
    sync_service = SpmSyncService(
        session,
        analyzer=SpmInventoryAnalyzer(
            session,
            external_enricher=enricher,
            schedule_background_tasks=False,
        ),
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            params=SpmEndpointSyncRequest(
                name="Chris MacBook",
                client_metadata={
                    "spm_policy": {
                        "approved_mcp_servers": [
                            {
                                "server_name": "github",
                                "resolved_identity": "https://allowed.example/mcp",
                            }
                        ]
                    }
                },
                assets=[
                    SpmSyncAssetUpsert(
                        harness=SpmHarness.CLAUDE_CODE,
                        asset_class=SpmAssetClass.MCP_SERVER,
                        asset_type=SpmAssetType.MCP_SERVER,
                        identity_key="file:/Users/chris/.claude.json#mcp:github|https://api.github.com/mcp",
                        display_name="github",
                        metadata={
                            "file_path": "/Users/chris/.claude.json",
                            "source_surface": "user_state_json",
                            "parse_status": "ok",
                            "server_name": "github",
                            "resolved_identity": "https://api.github.com/mcp",
                            "mcp_identity_key": "github|https://api.github.com/mcp",
                        },
                        evidence={"config": {"url": "https://api.github.com/mcp"}},
                        observed_state={"disabled": False},
                    )
                ],
            ),
        )

    findings = await _endpoint_findings(session, created.endpoint.id)
    approved_finding = _finding_for_control(findings, "claude.mcp_server.approved")
    assert approved_finding.status == SpmFindingStatus.OPEN.value
    assert (
        approved_finding.recommended_action
        == SpmEnforcementAction.DISABLE_MCP_SERVER.value
    )
    assert approved_finding.recommended_payload == {
        "server_name": "github",
        "resolved_identity": "https://api.github.com/mcp",
        "source_path": "/Users/chris/.claude.json",
        "project_root": None,
    }

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

    decision = await service.create_finding_decision(
        approved_finding.id,
        SpmFindingDecisionCreate(decision=SpmFindingDecisionType.ENFORCE),
    )
    assert decision.finding_id == approved_finding.id

    tasks = list((await session.scalars(select(SpmEnforcementTask))).all())
    assert len(tasks) == 1
    assert tasks[0].action == SpmEnforcementAction.DISABLE_MCP_SERVER.value
    assert tasks[0].payload["server_name"] == "github"
    assert tasks[0].payload["resolved_identity"] == "https://api.github.com/mcp"
    assert tasks[0].status == "pending"
    assert enricher.calls == []


@pytest.mark.anyio
async def test_sync_endpoint_creates_and_resolves_instruction_file_findings(
    session: AsyncSession,
    svc_role: Role,
) -> None:
    service = SpmService(session, role=svc_role)
    created = await service.create_endpoint(
        SpmEndpointCreate(
            name="Chris MacBook",
            harness=SpmHarness.CLAUDE_CODE,
            platform=SpmEndpointPlatform.MACOS,
        )
    )
    enricher = RecordingEnricher()
    sync_service = SpmSyncService(
        session,
        analyzer=SpmInventoryAnalyzer(
            session,
            external_enricher=enricher,
            schedule_background_tasks=False,
        ),
    )

    first_request = SpmEndpointSyncRequest(
        name="Chris MacBook",
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
                    "urls": ["https://bad.example"],
                    "domains": ["bad.example"],
                    "ips": [],
                    "indicator_reputation_status": "bad",
                },
                observed_state={"excluded": False},
            )
        ],
    )

    with patch(
        "tracecat_ee.spm.service.is_org_entitled",
        new=AsyncMock(return_value=True),
    ):
        first_response = await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=created.enrollment_token,
            params=first_request,
        )

        second_request = SpmEndpointSyncRequest(
            name="Chris MacBook",
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
                        "language_signal": {"likely_english": True},
                        "obfuscation": {"obfuscation_detected": False},
                        "urls": [],
                        "domains": [],
                        "ips": [],
                        "indicator_reputation_status": "good",
                    },
                    observed_state={"excluded": False},
                )
            ],
        )
        await sync_service.sync_endpoint(
            endpoint_id=created.endpoint.id,
            bearer_token=first_response.endpoint_secret or created.enrollment_token,
            params=second_request,
        )

    findings = await _endpoint_findings(session, created.endpoint.id)
    assert {finding.control_id: finding.status for finding in findings} == {
        "claude.instruction_file.external_indicators_reputation_ok": SpmFindingStatus.RESOLVED.value,
        "claude.instruction_file.language_english": SpmFindingStatus.RESOLVED.value,
        "claude.instruction_file.obfuscation_absent": SpmFindingStatus.RESOLVED.value,
    }
    assert len(enricher.calls) >= 1


async def _endpoint_findings(
    session: AsyncSession, endpoint_id: uuid.UUID
) -> Sequence[SpmFinding]:
    stmt = select(SpmFinding).where(SpmFinding.endpoint_id == endpoint_id)
    return list((await session.scalars(stmt)).all())


def _finding_for_control(findings: Sequence[SpmFinding], control_id: str) -> SpmFinding:
    for finding in findings:
        if finding.control_id == control_id:
            return finding
    raise AssertionError(f"Finding not found for control {control_id}")
