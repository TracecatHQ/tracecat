from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import uuid4

import orjson
from pydantic import BaseModel, Field

from tracecat.types.api import CaseContext, CaseParams, ListModel, Suppression, Tag

CaseEvent = Literal[
    "changed_status",
    "changed_priority",
    "added_comment",
    "opened_case",
    "closed_case",
]


class Case(BaseModel):
    """Case model used in the API and runner."""

    # Required inputs
    id: str = Field(default_factory=lambda: uuid4().hex)  # Action run id
    owner_id: str  # NOTE: Ideally this would inherit form db.Resource
    workflow_id: str
    title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    context: ListModel[CaseContext]  # JSON serialized
    action: Literal[
        "ignore", "quarantine", "informational", "sinkhole", "active_compromise"
    ]
    suppression: ListModel[Suppression]  # JSON serialized
    tags: ListModel[Tag]  # JSON serialized
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def flatten(self) -> dict[str, Any]:
        """Flattens nested object by JSON serializing object fields."""
        return {
            **self.model_dump(exclude={"context", "suppression", "tags", "payload"}),
            "context": self.context.model_dump_json(),
            "suppression": self.suppression.model_dump_json(),
            "tags": self.tags.model_dump_json(),
            "payload": orjson.dumps(self.payload).decode("utf-8")
            if self.payload
            else None,
        }

    @classmethod
    def from_flattened(cls, flat_dict: dict[str, Any]) -> Self:
        """Deserializes JSON fields."""
        flat_dict.update(
            context=orjson.loads(flat_dict["context"]),
            suppression=orjson.loads(flat_dict["suppression"]),
            tags=orjson.loads(flat_dict["tags"]),
            payload=orjson.loads(flat_dict["payload"])
            if flat_dict["payload"]
            else None,
        )
        return cls(**flat_dict)

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
