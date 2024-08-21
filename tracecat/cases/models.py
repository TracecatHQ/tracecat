from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from tracecat.identifiers import CaseID, OwnerID, WorkflowID


class CaseBase(BaseModel):
    # Case related fields
    owner_id: OwnerID
    workflow_id: WorkflowID
    case_title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    context: list[CaseContext]
    tags: list[Tag]


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


"""Case Events"""


class CaseEventParams(BaseModel):
    type: CaseEventType
    data: dict[str, str | None] | None


CaseEventType = Literal[
    "status_changed",
    "priority_changed",
    "comment_created",
    "case_opened",
    "case_closed",
]


class CaseResponse(BaseModel):
    id: str
    owner_id: OwnerID
    created_at: datetime
    updated_at: datetime
    workflow_id: str
    case_title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    context: list[CaseContext]
    tags: list[Tag]


class CaseParams(BaseModel):
    # SQLModel defaults
    id: str
    owner_id: OwnerID
    created_at: datetime
    updated_at: datetime
    # Case related fields
    workflow_id: str
    case_title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    context: list[CaseContext]
    tags: list[Tag]
