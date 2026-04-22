"""Domain types for AI SPM."""

from __future__ import annotations

from enum import StrEnum


class SpmHarness(StrEnum):
    """Normalized harness IDs supported by the SPM model."""

    CLAUDE_CODE = "claude_code"


class SpmEndpointPlatform(StrEnum):
    """Supported endpoint platforms."""

    MACOS = "macos"


class SpmEndpointStatus(StrEnum):
    """Endpoint lifecycle status."""

    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


class SpmAssetClass(StrEnum):
    """Harness-agnostic asset taxonomy."""

    WORKSPACE_ACCESS = "workspace_access"
    PERMISSIONS = "permissions"
    SANDBOX = "sandbox"
    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    EXTENSION = "extension"
    INSTRUCTION_FILE = "instruction_file"
    AGENT = "agent"


class SpmAssetType(StrEnum):
    """Harness-native governed surfaces."""

    TRUSTED_DIRECTORY = "trusted_directory"
    ADDITIONAL_DIRECTORY = "additional_directory"
    PERMISSION_CONFIG = "permission_config"
    SANDBOX_CONFIG = "sandbox_config"
    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    HOOK = "hook"
    CLAUDE_MD = "claude_md"
    AGENTS_MD = "agents_md"
    SUBAGENT = "subagent"


class SpmSeverity(StrEnum):
    """Normalized SPM severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SpmControlCheck(StrEnum):
    """Registered SPM control evaluation keys."""

    TRUSTED_DIRECTORY_APPROVED = "trusted_directory_approved"
    ADDITIONAL_DIRECTORY_APPROVED = "additional_directory_approved"
    PERMISSION_CONFIG_APPROVED = "permission_config_approved"
    SANDBOX_CONFIG_APPROVED = "sandbox_config_approved"
    MCP_SERVER_APPROVED = "mcp_server_approved"
    MCP_SERVER_VULNERABILITY_OK = "mcp_server_vulnerability_ok"
    MCP_SERVER_REPUTATION_OK = "mcp_server_reputation_ok"
    SKILL_APPROVED = "skill_approved"
    HOOK_APPROVED = "hook_approved"
    INSTRUCTION_FILE_LANGUAGE_ENGLISH = "instruction_file_language_english"
    INSTRUCTION_FILE_OBFUSCATION_ABSENT = "instruction_file_obfuscation_absent"
    INSTRUCTION_FILE_EXTERNAL_INDICATORS_REPUTATION_OK = (
        "instruction_file_external_indicators_reputation_ok"
    )


class SpmFindingStatus(StrEnum):
    """Current lifecycle state of a finding."""

    OPEN = "open"
    DISMISSED = "dismissed"
    ENFORCEMENT_PENDING = "enforcement_pending"
    ENFORCED = "enforced"
    RESOLVED = "resolved"


class SpmFindingDecisionType(StrEnum):
    """Operator decisions recorded against findings."""

    DISMISS = "dismiss"
    ENFORCE = "enforce"
    REOPEN = "reopen"


class SpmEnforcementAction(StrEnum):
    """Supported enforcement actions."""

    DISABLE_MCP_SERVER = "disable_mcp_server"
    EXCLUDE_INSTRUCTION_FILE = "exclude_instruction_file"
    REVOKE_TRUSTED_DIRECTORY = "revoke_trusted_directory"
    REVOKE_ADDITIONAL_DIRECTORY = "revoke_additional_directory"
    RECONCILE_PERMISSION_CONFIG = "reconcile_permission_config"
    RECONCILE_SANDBOX_CONFIG = "reconcile_sandbox_config"
    DISABLE_HOOK = "disable_hook"
    DISABLE_SKILL = "disable_skill"


class SpmEnforcementTaskStatus(StrEnum):
    """Execution state for endpoint enforcement tasks."""

    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"


class SpmSyncTaskResultStatus(StrEnum):
    """Status reported by an endpoint during sync."""

    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"
