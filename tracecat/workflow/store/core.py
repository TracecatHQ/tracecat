from dataclasses import dataclass

from tracecat.identifiers.workflow import WorkflowID
from tracecat.store import ExternalStore, Source

WorkflowSource = Source[WorkflowID]
WorkflowStore = ExternalStore[WorkflowSource]


@dataclass(frozen=True)
class PublishRequest:
    repo_url: str
    ref: str | None = None
    message: str | None = None
