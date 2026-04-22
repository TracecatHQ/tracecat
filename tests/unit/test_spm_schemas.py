"""Schema tests for AI SPM contracts."""

import uuid

from tracecat_ee.spm.schemas import SpmSyncTaskResult
from tracecat_ee.spm.types import (
    SpmEnforcementAction,
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
