"""Schema tests for AI SPM contracts."""

import uuid
from datetime import UTC, datetime

from tracecat_ee.spm.schemas import SpmEndpointAssetRead, SpmSyncTaskResult
from tracecat_ee.spm.types import (
    SpmAssetClass,
    SpmAssetType,
    SpmEnforcementAction,
    SpmHarness,
    SpmSyncTaskResultStatus,
)


def test_sync_task_result_defaults_completed_at() -> None:
    """Sync task results should self-stamp completion when omitted."""
    task_result = SpmSyncTaskResult(
        task_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        status=SpmSyncTaskResultStatus.APPLIED,
    )

    assert task_result.completed_at is not None


def test_enforcement_action_enum_contains_locked_v1_actions() -> None:
    """The backend task model must expose the locked v1 enforcement actions."""
    assert SpmEnforcementAction.DISABLE_MCP_SERVER.value == "disable_mcp_server"
    assert (
        SpmEnforcementAction.EXCLUDE_INSTRUCTION_FILE.value
        == "exclude_instruction_file"
    )
    assert (
        SpmEnforcementAction.REVOKE_TRUSTED_DIRECTORY.value
        == "revoke_trusted_directory"
    )
    assert (
        SpmEnforcementAction.REVOKE_ADDITIONAL_DIRECTORY.value
        == "revoke_additional_directory"
    )


def test_endpoint_asset_read_accepts_asset_metadata_alias() -> None:
    """Endpoint asset reads should accept joined asset metadata rows."""
    now = datetime(2026, 4, 22, tzinfo=UTC)
    row = SpmEndpointAssetRead.model_validate(
        {
            "asset_id": uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
            "asset_sighting_id": uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
            "organization_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "endpoint_id": uuid.UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc"),
            "workspace_id": None,
            "harness": SpmHarness.CLAUDE_CODE,
            "asset_class": SpmAssetClass.INSTRUCTION_FILE,
            "asset_type": SpmAssetType.CLAUDE_MD,
            "identity_key": "/Users/chris/project/CLAUDE.md",
            "display_name": "CLAUDE.md",
            "content_hash": "abc123",
            "asset_metadata": {"file_path": "/Users/chris/project/CLAUDE.md"},
            "evidence": {"language_signal": {"likely_english": False}},
            "observed_state": {"excluded": True},
            "first_seen_at": now,
            "last_seen_at": now,
        }
    )

    assert row.metadata == {"file_path": "/Users/chris/project/CLAUDE.md"}
    assert row.observed_state == {"excluded": True}
