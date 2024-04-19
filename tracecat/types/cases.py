from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import uuid4

import orjson
from pydantic import BaseModel, Field

from tracecat.types.api import CaseContext, CaseParams, ListModel, Suppression, Tag


class Case(BaseModel):
    # Required inputs
    id: str = Field(default_factory=lambda: uuid4().hex)  # Action run id
    owner_id: str  # NOTE: Ideally this would inherit form db.Resource
    workflow_id: str
    title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    # Optional inputs (can be AI suggested)
    context: ListModel[CaseContext] | None = None
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    suppression: ListModel[Suppression] | None = None  # JSON serialized
    tags: ListModel[Tag] | None = None  # JSON serialized
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def flatten(self) -> dict[str, Any]:
        """Flattens nested object by JSON serializing object fields."""
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "workflow_id": self.workflow_id,
            "title": self.title,
            "payload": orjson.dumps(self.payload).decode("utf-8")
            if self.payload
            else None,
            "context": self.context.model_dump_json() if self.context else None,
            "malice": self.malice,
            "priority": self.priority,
            "status": self.status,
            "action": self.action,
            "suppression": self.suppression.model_dump_json()
            if self.suppression
            else None,
            "tags": self.tags.model_dump_json() if self.tags else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_flattened(cls, flat_dict: dict[str, Any]) -> Self:
        """Deserializes JSON fields."""
        return cls(
            id=flat_dict["id"],
            owner_id=flat_dict["owner_id"],
            workflow_id=flat_dict["workflow_id"],
            title=flat_dict["title"],
            payload=orjson.loads(flat_dict["payload"])
            if flat_dict["payload"]
            else None,
            context=orjson.loads(flat_dict["context"])
            if flat_dict["context"]
            else None,
            malice=flat_dict["malice"],
            priority=flat_dict["priority"],
            status=flat_dict["status"],
            action=flat_dict["action"],
            suppression=orjson.loads(flat_dict["suppression"])
            if flat_dict["suppression"]
            else None,
            tags=orjson.loads(flat_dict["tags"]) if flat_dict["tags"] else None,
            created_at=flat_dict["created_at"],
            updated_at=flat_dict["updated_at"],
        )

    @classmethod
    def from_params(
        cls, params: CaseParams, *, owner_id: str, id: str | None = None
    ) -> "Case":
        """Constructs from API params."""
        kwargs = {"owner_id": owner_id}
        kwargs.update(params.model_dump())
        if id:
            kwargs["id"] = id
        return cls(**kwargs)


class CaseMetrics(BaseModel):
    """Summary statistics for cases over a time period."""

    statues: list[dict[str, int | float]]
    priority: list[dict[str, int | float]]
    malice: list[dict[str, int | float]]
