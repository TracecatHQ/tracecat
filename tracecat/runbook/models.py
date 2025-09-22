"""Runbook API models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import UUID4, BaseModel, Field, StringConstraints

type RunbookAlias = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]+$")]


class RunbookCreate(BaseModel):
    """Request model for creating a runbook."""

    chat_id: UUID4 | None = Field(
        default=None,
        description="ID of the chat to freeze into a runbook",
    )
    alias: RunbookAlias | None = Field(
        default=None,
        description="Alias for the runbook",
        min_length=3,
        max_length=50,
    )


class RunbookRead(BaseModel):
    """Model for runbook details."""

    id: UUID4 = Field(..., description="Unique runbook identifier")
    title: str = Field(..., description="Human-readable title for the runbook")
    tools: list[str] = Field(
        ...,
        description="The tools available to the agent for this runbook",
    )
    created_at: datetime = Field(..., description="When the runbook was created")
    updated_at: datetime = Field(..., description="When the runbook was last updated")
    instructions: str = Field(
        ...,
        description="The instructions for the runbook",
        min_length=1,
        max_length=10000,
    )
    related_cases: list[UUID4] | None = Field(
        ..., description="The cases that the runbook is related to"
    )
    alias: RunbookAlias | None = Field(
        default=None,
        description="Alias for the runbook",
    )


class RunbookUpdate(BaseModel):
    """Request model for updating runbook properties."""

    title: str | None = Field(
        default=None,
        description="New title for the runbook",
        min_length=1,
        max_length=200,
    )
    tools: list[str] | None = Field(
        default=None,
        description="New tools for the runbook",
    )
    instructions: str | None = Field(
        default=None,
        description="New instructions for the runbook",
        min_length=1,
        max_length=10000,
    )
    related_cases: list[UUID4] | None = Field(
        default=None,
        description="New related cases for the runbook",
    )
    alias: RunbookAlias | None = Field(
        default=None,
        description="New alias for the runbook (must be unique within workspace)",
        min_length=3,
        max_length=50,
    )


class RunbookExecuteRequest(BaseModel):
    """Request model for executing a runbook on cases."""

    case_ids: list[UUID4] = Field(
        ..., description="IDs of the cases to execute the runbook on"
    )


class RunbookExecuteResponse(BaseModel):
    """Response model for executing a runbook on cases."""

    stream_urls: dict[str, str] = Field(
        ..., description="Mapping of case ID to stream URL"
    )
