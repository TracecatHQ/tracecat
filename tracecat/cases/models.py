from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.identifiers import CaseID, OwnerID, WorkflowID

CaseMalice = Literal[
    "malicious",
    "benign",
]
CaseStatus = Literal[
    "open",
    "closed",
    "in_progress",
    "reported",
    "escalated",
]
CasePriority = Literal[
    "low",
    "medium",
    "high",
    "critical",
]
CaseAction = Literal[
    "ignore",
    "quarantine",
    "informational",
    "sinkhole",
    "active_compromise",
]


class CaseBase(BaseModel):
    # Immutable
    owner_id: OwnerID
    workflow_id: WorkflowID
    case_title: str
    payload: dict[str, Any]
    context: list[CaseContext]
    tags: list[Tag]
    # Mutable
    malice: CaseMalice
    status: CaseStatus
    priority: CasePriority
    action: CaseAction


class CaseCreate(CaseBase):
    pass


class CaseRead(CaseBase):
    # SQLModel defaults
    id: CaseID
    created_at: datetime
    updated_at: datetime


class CaseContext(BaseModel):
    key: str
    value: str


class Tag(BaseModel):
    tag: str
    value: str


class CaseUpdate(BaseModel):
    malice: CaseMalice | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    action: CaseAction | None = None


"""Case Events"""


CaseEventType = Literal[
    "status_changed",
    "priority_changed",
    "comment_created",
    "case_opened",
    "case_closed",
]


class CaseEventCreate(BaseModel):
    type: CaseEventType
    data: dict[str, str | None] | None
