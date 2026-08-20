"""API schemas for immutable case text-field versions."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from tracecat.cases.enums import CaseVersionField
from tracecat.core.schemas import Schema


class CaseVersionActorRead(Schema):
    """Minimal user metadata for a case-version author."""

    id: uuid.UUID
    email: str
    first_name: str | None = Field(default=None)
    last_name: str | None = Field(default=None)


class CaseVersionReadMinimal(Schema):
    """Version metadata returned by the case history endpoint."""

    id: uuid.UUID
    field: CaseVersionField
    version: int
    actor: CaseVersionActorRead | None = Field(default=None)
    created_at: datetime
    is_latest: bool = Field(
        description="Whether this is the latest immutable version for its field"
    )


class CaseVersionContentRead(Schema):
    """Content for one immutable case field version."""

    id: uuid.UUID
    field: CaseVersionField
    version: int
    content: str


class CaseVersionCompareRead(Schema):
    """A selected case version and its immediate same-field predecessor."""

    selected: CaseVersionContentRead
    predecessor: CaseVersionContentRead | None = Field(default=None)


class CaseVersionRestoreRead(Schema):
    """Confirmation that a historical case field version was restored."""

    restored: bool = Field(default=True)
    case_id: uuid.UUID
    restored_from_version_id: uuid.UUID
    field: CaseVersionField
