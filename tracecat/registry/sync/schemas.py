"""
Type-safe schemas for subprocess sync communication.

This module defines Pydantic models used to serialize/deserialize data
between the sync subprocess and the main API process.
"""

from __future__ import annotations

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
