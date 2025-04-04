from typing import Any

from pydantic import BaseModel, Field

from tracecat.dsl.models import ExecutionContext


class ObjectRef(BaseModel):
    """A reference to an object. This goes into the temporalio.Payload.data field."""

    metadata: dict[str, Any] = Field(default_factory=lambda: {"encoding": "json/plain"})
    """Metadata about the object."""

    size: int
    """The size of the object in bytes."""

    digest: str
    """The digest of the object."""

    key: str
    """The key of the object."""


def as_object_ref(value: Any) -> ObjectRef | None:
    """Try to convert a value to an ObjectRef."""
    try:
        return ObjectRef.model_validate(value)
    except Exception:
        return None


class StoreWorkflowResultActivityInput(BaseModel):
    """Input for the store workflow result activity."""

    args: Any
    """The arguments to the workflow."""

    context: ExecutionContext
    """The context of the workflow."""


class ResolveObjectRefsActivityInput(BaseModel):
    """Input for the resolve object refs activity."""

    obj: Any
    """The arguments to the workflow."""

    context: ExecutionContext
    """The context of the workflow."""


class ResolveConditionActivityInput(BaseModel):
    """Input for the resolve run if activity."""

    context: ExecutionContext
    """The context of the workflow."""

    condition_expr: str
    """The condition expression to evaluate."""


class ResolveObjectActivityInput(BaseModel):
    """Input for the resolve run if activity."""

    context: ExecutionContext
    """The context of the workflow."""

    obj: Any
    """The object to resolve."""
