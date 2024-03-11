from typing import Any, Literal

import orjson
from pydantic import BaseModel


class Case(BaseModel):
    # Required inputs
    title: str
    payload: dict[str, Any]
    malice: Literal["malicious", "benign"]
    status: Literal["open", "closed", "in_progress", "reported", "escalated"]
    priority: Literal["low", "medium", "high", "critical"]
    # Optional inputs (can be AI suggested)
    context: dict[str, str] | None = None
    action: str | None = None
    suppression: dict[str, bool] | None = None

    def flatten(self):
        """Flattens nested object by JSON serializing object fields."""
        return {
            "title": self.title,
            "payload": orjson.dumps(self.payload).decode("utf-8")
            if self.payload
            else None,
            "context": orjson.dumps(self.context).decode("utf-8")
            if self.payload
            else None,
            "malice": self.malice,
            "priority": self.priority,
            "status": self.status,
            "action": self.action,
            "suppression": orjson.dumps(self.suppression).decode("utf-8")
            if self.payload
            else None,
        }
