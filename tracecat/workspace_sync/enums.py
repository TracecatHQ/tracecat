"""Workspace sync enum values."""

from __future__ import annotations

from enum import StrEnum


class VcsProvider(StrEnum):
    """Version control host backing a workspace sync repository."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"


class SyncResourceType(StrEnum):
    """Kind of workspace resource that can be synced to and from Git."""

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
