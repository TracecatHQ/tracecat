"""Workspace sync enum values."""

from __future__ import annotations

from enum import StrEnum


class VcsProvider(StrEnum):
    """Version control host backing a workspace sync repository."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"


class SyncResourceType(StrEnum):
    """Kind of workspace resource that can be synced to and from Git.

    Every member is adapter-backed: it can be projected to and imported from
    repository files. Reference-only correlation targets belong in
    :class:`ReferenceKind` instead.
    """

    WORKFLOW = "workflow"
    AGENT_PRESET = "agent_preset"
    SKILL = "skill"
    TABLE = "table"
    CASE_TAG = "case_tag"
    CASE_FIELD = "case_field"
    CASE_DROPDOWN = "case_dropdown"
    CASE_DURATION = "case_duration"
    VARIABLE = "variable"
    SECRET_METADATA = "secret_metadata"


class ReferenceKind(StrEnum):
    """Workspace-local target referenced by synced documents but never synced itself.

    Values share the mapping table's ``resource_type`` string column with
    :class:`SyncResourceType` and must not collide with it.
    """

    MCP_INTEGRATION = "mcp_integration"
