"""
Type-safe schemas for registry sync communication.

This module defines Pydantic models used to serialize/deserialize data
for registry sync operations, including:
- Subprocess sync (existing)
- Temporal workflow sync (sandboxed executor)
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionValidationErrorInfo,
)


class SyncResultSuccess(BaseModel):
    """Successful sync result containing actions and metadata."""

    actions: list[RegistryActionCreate] = Field(
        default_factory=list,
        description="List of serialized registry actions.",
    )
    commit_sha: str | None = Field(
        default=None,
        description="The resolved commit SHA (None for local/builtin repos).",
    )
    validation_errors: dict[str, list[RegistryActionValidationErrorInfo]] = Field(
        default_factory=dict,
        description="Map of action name to list of validation errors.",
    )


class SyncResultError(BaseModel):
    """Error result from sync subprocess."""

    error: str = Field(..., description="Error message from the subprocess.")


# Type adapter for parsing the sync result (success or error)
SyncResultAdapter: TypeAdapter[SyncResultSuccess | SyncResultError] = TypeAdapter(
    SyncResultSuccess | SyncResultError
)


# =============================================================================
# Temporal Workflow Schemas (Sandboxed Registry Sync)
# =============================================================================


class RegistrySyncRequest(BaseModel):
    """Request for sandboxed registry sync via Temporal workflow.

    This is passed from the API service to the ExecutorWorker via Temporal.
    The SSH key is used for git clone only and never enters the nsjail sandbox.
    """

    repository_id: UUID = Field(..., description="Database repository ID")
    origin: str = Field(..., description="Repository origin URL or name")
    origin_type: Literal["builtin", "local", "git"] = Field(
        ..., description="Type of repository origin"
    )
    git_url: str | None = Field(
        default=None, description="Git SSH URL for cloning (git origins only)"
    )
    commit_sha: str | None = Field(
        default=None, description="Target commit SHA (git origins only)"
    )
    git_repo_package_name: str | None = Field(
        default=None,
        description="Optional Python package name override for git repositories",
    )
    ssh_key: str | None = Field(
        default=None,
        description="SSH private key for git clone (never enters nsjail sandbox)",
    )
    validate_actions: bool = Field(
        default=False, description="Whether to validate template actions"
    )
    storage_namespace: str | None = Field(
        default=None,
        description=(
            "Storage namespace for tarball uploads (e.g., org ID or 'platform'). "
            "Defaults to the deployment's default org ID when not provided."
        ),
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID for org-scoped operations (e.g., secrets access).",
    )


class RegistrySyncResult(BaseModel):
    """Result from sandboxed registry sync workflow.

    Returned from the ExecutorWorker to the API service via Temporal.
    Contains discovered actions and tarball location for DB operations.
    """

    actions: list[RegistryActionCreate] = Field(
        default_factory=list,
        description="List of discovered registry actions",
    )
    tarball_uri: str = Field(..., description="S3 URI of the uploaded tarball venv")
    commit_sha: str | None = Field(
        default=None,
        description="Resolved commit SHA (None for builtin/local repos)",
    )
    validation_errors: dict[str, list[RegistryActionValidationErrorInfo]] = Field(
        default_factory=dict,
        description="Map of action name to validation errors",
    )
