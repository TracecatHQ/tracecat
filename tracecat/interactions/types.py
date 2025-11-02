from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tracecat.interactions.enums import InteractionStatus, InteractionType


@dataclass
class InteractionState:
    """Runtime state for workflow interactions."""

    type: InteractionType
    action_ref: str
    status: InteractionStatus
    data: dict[str, Any] = field(default_factory=dict)

    def is_activated(self) -> bool:
        return self.status == InteractionStatus.COMPLETED
