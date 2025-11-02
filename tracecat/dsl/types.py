from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from tracecat.expressions.common import ExprContext

if TYPE_CHECKING:
    from tracecat.dsl.schemas import StreamID
else:  # pragma: no cover - runtime import to avoid circular dependency
    StreamID = Any  # type: ignore[assignment]


def _root_stream_factory() -> StreamID:
    from tracecat.dsl.schemas import ROOT_STREAM

    return ROOT_STREAM


@dataclass(frozen=True)
class TaskExceptionInfo:
    exception: Exception
    details: ActionErrorInfo


@dataclass(frozen=True, slots=True)
class Task:
    """Stream-aware task instance."""

    ref: str
    stream_id: StreamID
    delay: float = field(default=0.0, compare=False)


@dataclass(frozen=True, slots=True)
class ActionErrorInfo:
    """Contains information about an action error."""

    ref: str
    message: str
    type: str
    expr_context: ExprContext = ExprContext.ACTIONS
    attempt: int = 1
    stream_id: StreamID = field(default_factory=_root_stream_factory)
    children: list[ActionErrorInfo] | None = None

    def format(self, loc: str = "run_action") -> str:
        locator = f"{self.expr_context}.{self.ref} -> {loc}"
        return f"[{locator}] (Attempt {self.attempt})\n\n{self.message}"


ActionErrorInfoAdapter = TypeAdapter(ActionErrorInfo)
