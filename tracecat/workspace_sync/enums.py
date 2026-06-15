"""Workspace sync enum values."""

from __future__ import annotations

from enum import StrEnum


class VcsProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"


class SyncResourceType(StrEnum):
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
