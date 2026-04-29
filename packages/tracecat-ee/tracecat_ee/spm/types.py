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


class SpmAssetType(StrEnum):
    """Harness surface bucket. The broad kind of governed thing."""

    HOOK = "hook"
    PLUGIN = "plugin"
    MCP_SERVER = "mcp_server"
    INSTRUCTION_FILE = "instruction_file"
    PERMISSION_CONFIG = "permission_config"
    SANDBOX_CONFIG = "sandbox_config"
    TRUSTED_DIRECTORY = "trusted_directory"
    ADDITIONAL_DIRECTORY = "additional_directory"
    SKILL = "skill"
    AGENT = "agent"


class SpmArtifactType(StrEnum):
    """The file kind that hosts an asset."""

    SETTINGS_JSON = "settings.json"
    SETTINGS_LOCAL_JSON = "settings.local.json"
    CLAUDE_JSON = ".claude.json"
    HOOKS_JSON = "hooks.json"
    MCP_JSON = ".mcp.json"
    CLAUDE_MD = "CLAUDE.md"
    CLAUDE_LOCAL_MD = "CLAUDE.local.md"
    AGENTS_MD = "AGENTS.md"
    SKILL_FRONTMATTER = "skill-frontmatter"
    AGENT_FRONTMATTER = "agent-frontmatter"
    PLUGIN_MANIFEST = "plugin.json"
    DIRECTORY = "directory"


class SpmSeverity(StrEnum):
    """Normalized SPM severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


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
