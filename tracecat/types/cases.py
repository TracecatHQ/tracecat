from typing import Any, Literal, Self

import orjson
from pydantic import BaseModel


class Case(BaseModel):
    # Required inputs
    id: str  # Action run id
    workflow_id: str
    title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    # Optional inputs (can be AI suggested)
    context: dict[str, str] | None = None
    action: str | None = None
    suppression: dict[str, bool] | None = None

    def flatten(self) -> dict[str, Any]:
        """Flattens nested object by JSON serializing object fields."""
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "title": self.title,
            "payload": orjson.dumps(self.payload).decode("utf-8")
            if self.payload
            else None,
            "context": orjson.dumps(self.context).decode("utf-8")
            if self.context
            else None,
            "malice": self.malice,
            "priority": self.priority,
            "status": self.status,
            "action": self.action,
            "suppression": orjson.dumps(self.suppression).decode("utf-8")
            if self.suppression
            else None,
        }

    @classmethod
    def from_flattened(cls, flat_dict: dict[str, Any]) -> Self:
        """Deserializes JSON fields."""
        return cls(
            id=flat_dict["id"],
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
        )


class CaseMetrics(BaseModel):
    """Summary statistics for cases over a time period."""

    statues: list[dict[str, int | float]]
    priority: list[dict[str, int | float]]
    malice: list[dict[str, int | float]]
