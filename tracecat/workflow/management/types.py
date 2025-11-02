from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class WorkflowDefinitionMinimal:
    """Workflow definition metadata domain model."""

    id: str
    version: int
    created_at: datetime
