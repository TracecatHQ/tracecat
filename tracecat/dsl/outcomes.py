"""ActionOutcome: Tagged union for action execution results.

This module defines a discriminated union type for representing the result of
executing an action statement. It provides explicit status tracking and prepares
for future large-payload externalization.

Design Goals:
- Preserve backwards compatibility with existing template expressions:
  - `${{ ACTIONS.a.result }}` still works
  - `${{ ACTIONS.a.error }}` still works
  - New: `${{ ACTIONS.a.status }}` for branching/telemetry
- Keep envelope small; prepare for "large payload refs"
- Make action execution return a single, explicit outcome per ActionStatement
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

if TYPE_CHECKING:
    pass


# -----------------------------------------------------------------------------
# ResultRef: Placeholder for future large-payload externalization
# -----------------------------------------------------------------------------


class ResultRef(BaseModel):
    """Reference to an externalized result (S3, DB, etc.).

    When `result_ref` is present on an outcome, `result` may be omitted or
    contain only a small preview. This prepares for large-payload handling
    without inflating Temporal history.
    """

    backend: Literal["s3", "db", "inline"]
    """Storage backend for the externalized result."""

    key: str
    """Unique identifier/path for the stored result."""

    size_bytes: int | None = None
    """Size of the stored result in bytes."""

    sha256: str | None = None
    """SHA-256 hash of the stored result for integrity verification."""


# -----------------------------------------------------------------------------
# ManifestRef: For scatter collections
# -----------------------------------------------------------------------------


class ManifestRef(BaseModel):
    """Reference to an externalized scatter manifest.

    Instead of embedding N items inline in history, scatter stores a manifest
    and returns a count. Per-stream items are represented as small refs.
    """

    backend: Literal["s3", "db", "inline"]
    """Storage backend for the manifest."""

    key: str
    """Unique identifier/path for the manifest."""

    size_bytes: int | None = None
    """Size of the manifest in bytes."""


# -----------------------------------------------------------------------------
# ActionOutcome Variants
# -----------------------------------------------------------------------------


class ActionOutcomeSuccess(BaseModel):
    """Outcome for a successfully executed action.

    Attributes:
        status: Always "success" for this variant.
        result: The action's return value.
        result_typename: Python type name of the result (e.g., "dict", "list").
        result_ref: Optional reference if result is externalized.
        interaction: Optional interaction data if action was interactive.
        interaction_id: Optional interaction identifier.
        interaction_type: Optional interaction type.
    """

    status: Literal["success"] = "success"
    result: Any = None
    result_typename: str = "NoneType"
    result_ref: ResultRef | None = None
    interaction: Any = None
    interaction_id: str | None = None
    interaction_type: str | None = None

    # Backwards compatibility properties
    @property
    def error(self) -> None:
        """Backwards compatibility: success outcomes have no error."""
        return None

    @property
    def error_typename(self) -> None:
        """Backwards compatibility: success outcomes have no error typename."""
        return None


class ActionOutcomeError(BaseModel):
    """Outcome for an action that failed after exhausting retries.

    Attributes:
        status: Always "error" for this variant.
        error: Error details (ActionErrorInfo or raw error data).
        error_typename: Python type name of the error.
    """

    status: Literal["error"] = "error"
    error: Any = None  # ActionErrorInfo | Any - kept as Any for serialization
    error_typename: str = "Exception"

    # Backwards compatibility: error outcomes return None for result
    @property
    def result(self) -> None:
        """Backwards compatibility: error outcomes have no result."""
        return None

    @property
    def result_typename(self) -> str:
        """Backwards compatibility: error outcomes have NoneType result."""
        return "NoneType"


class ActionOutcomeSkipped(BaseModel):
    """Outcome for an action that was skipped (run_if=false or propagation).

    Attributes:
        status: Always "skipped" for this variant.
        reason: Optional human-readable reason for the skip.
    """

    status: Literal["skipped"] = "skipped"
    reason: str | None = None

    # Backwards compatibility properties
    @property
    def result(self) -> None:
        """Backwards compatibility: skipped outcomes have no result."""
        return None

    @property
    def result_typename(self) -> str:
        """Backwards compatibility: skipped outcomes have NoneType result."""
        return "NoneType"

    @property
    def error(self) -> None:
        """Backwards compatibility: skipped outcomes have no error."""
        return None

    @property
    def error_typename(self) -> None:
        """Backwards compatibility: skipped outcomes have no error typename."""
        return None


# -----------------------------------------------------------------------------
# Control-Flow Action Outcomes (Scatter/Gather)
# -----------------------------------------------------------------------------


class ActionOutcomeScatter(BaseModel):
    """Outcome for a scatter control-flow action.

    Scatter evaluates a collection and creates execution streams. The actual
    items are stored in a manifest to avoid history bloat.

    Attributes:
        status: Always "scatter" for this variant (represents successful scatter).
        count: Number of items/streams created.
        manifest_ref: Reference to externalized collection manifest.
        result: For backwards compat, contains the stream IDs or count.
        result_typename: Type name of result.
    """

    status: Literal["scatter"] = "scatter"
    count: int = 0
    manifest_ref: ManifestRef | None = None
    result: Any = None  # Stream IDs or count for backwards compat
    result_typename: str = "int"

    @property
    def error(self) -> None:
        """Backwards compatibility: scatter outcomes have no error."""
        return None

    @property
    def error_typename(self) -> None:
        """Backwards compatibility: scatter outcomes have no error typename."""
        return None


class ActionOutcomeGather(BaseModel):
    """Outcome for a gather control-flow action.

    Gather synchronizes execution streams and collects results.

    Attributes:
        status: Always "gather" for this variant (represents successful gather).
        result: Gathered results (list of items from streams).
        result_typename: Type name of result.
        result_ref: Optional reference if result is externalized.
        error: Optional list of errors from streams (if error_strategy=partition).
        error_typename: Type name of errors if present.
    """

    status: Literal["gather"] = "gather"
    result: Any = None
    result_typename: str = "list"
    result_ref: ResultRef | None = None
    error: list[Any] | None = (
        None  # list[ActionErrorInfo] - kept as Any for serialization
    )
    error_typename: str | None = None


# -----------------------------------------------------------------------------
# ActionOutcome Union Type
# -----------------------------------------------------------------------------

ActionOutcome = Annotated[
    ActionOutcomeSuccess
    | ActionOutcomeError
    | ActionOutcomeSkipped
    | ActionOutcomeScatter
    | ActionOutcomeGather,
    Field(discriminator="status"),
]
"""Tagged union of all possible action outcomes.

Use the `status` field to discriminate between variants:
- "success": ActionOutcomeSuccess or ActionOutcomeScatter/Gather (check `type` field)
- "error": ActionOutcomeError
- "skipped": ActionOutcomeSkipped
"""

# Type adapter for serialization/deserialization
ActionOutcomeAdapter: TypeAdapter[ActionOutcome] = TypeAdapter(ActionOutcome)


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def outcome_to_dict(outcome: ActionOutcome) -> dict[str, Any]:
    """Convert an ActionOutcome to a dictionary for serialization.

    This preserves backwards compatibility with code expecting TaskResult-like dicts.
    """
    return outcome.model_dump()


def dict_to_outcome(data: dict[str, Any]) -> ActionOutcome:
    """Convert a dictionary to an ActionOutcome.

    Uses the discriminator field to determine the correct variant.
    """
    return ActionOutcomeAdapter.validate_python(data)


def is_success(outcome: ActionOutcome) -> bool:
    """Check if an outcome represents successful execution.

    Returns True for success, scatter, and gather outcomes since they all
    represent successful execution of their respective actions.
    """
    return outcome.status in ("success", "scatter", "gather")


def is_error(outcome: ActionOutcome) -> bool:
    """Check if an outcome represents an error."""
    return outcome.status == "error"


def is_skipped(outcome: ActionOutcome) -> bool:
    """Check if an outcome represents a skipped action."""
    return outcome.status == "skipped"


def is_scatter(outcome: ActionOutcome) -> bool:
    """Check if an outcome is from a scatter action."""
    return isinstance(outcome, ActionOutcomeScatter)


def is_gather(outcome: ActionOutcome) -> bool:
    """Check if an outcome is from a gather action."""
    return isinstance(outcome, ActionOutcomeGather)


# -----------------------------------------------------------------------------
# Factory Functions for Creating Outcomes
# -----------------------------------------------------------------------------


def success(
    result: Any = None,
    result_typename: str | None = None,
    *,
    result_ref: ResultRef | None = None,
    interaction: Any = None,
    interaction_id: str | None = None,
    interaction_type: str | None = None,
) -> ActionOutcomeSuccess:
    """Create a success outcome."""
    return ActionOutcomeSuccess(
        result=result,
        result_typename=result_typename or type(result).__name__,
        result_ref=result_ref,
        interaction=interaction,
        interaction_id=interaction_id,
        interaction_type=interaction_type,
    )


def error(
    err: Any = None,
    error_typename: str | None = None,
) -> ActionOutcomeError:
    """Create an error outcome."""
    return ActionOutcomeError(
        error=err,
        error_typename=error_typename or type(err).__name__,
    )


def skipped(reason: str | None = None) -> ActionOutcomeSkipped:
    """Create a skipped outcome."""
    return ActionOutcomeSkipped(reason=reason)


def scatter(
    count: int,
    result: Any = None,
    *,
    manifest_ref: ManifestRef | None = None,
) -> ActionOutcomeScatter:
    """Create a scatter outcome."""
    return ActionOutcomeScatter(
        count=count,
        manifest_ref=manifest_ref,
        result=result if result is not None else count,
        result_typename=type(result).__name__ if result is not None else "int",
    )


def gather(
    result: Any = None,
    *,
    result_ref: ResultRef | None = None,
    errors: list[Any] | None = None,
) -> ActionOutcomeGather:
    """Create a gather outcome."""
    return ActionOutcomeGather(
        result=result,
        result_typename=type(result).__name__ if result is not None else "list",
        result_ref=result_ref,
        error=errors,
        error_typename="list" if errors else None,
    )


# -----------------------------------------------------------------------------
# TaskResult Compatibility Layer
# -----------------------------------------------------------------------------


def from_task_result(task_result: dict[str, Any]) -> ActionOutcome:
    """Convert a legacy TaskResult dict to an ActionOutcome.

    This provides backwards compatibility during migration.

    Args:
        task_result: A dict with TaskResult structure (result, result_typename, error?, etc.)

    Returns:
        The appropriate ActionOutcome variant based on the dict contents.
    """
    # If it already has a status field, it's already an ActionOutcome dict
    if "status" in task_result:
        return ActionOutcomeAdapter.validate_python(task_result)

    # Check for error to determine if this is an error outcome
    if "error" in task_result and task_result.get("error") is not None:
        return ActionOutcomeError(
            error=task_result.get("error"),
            error_typename=task_result.get("error_typename", "Exception"),
        )

    # Otherwise it's a success outcome
    return ActionOutcomeSuccess(
        result=task_result.get("result"),
        result_typename=task_result.get("result_typename", "NoneType"),
        interaction=task_result.get("interaction"),
        interaction_id=task_result.get("interaction_id"),
        interaction_type=task_result.get("interaction_type"),
    )


def to_task_result_dict(outcome: ActionOutcome) -> dict[str, Any]:
    """Convert an ActionOutcome to a TaskResult-compatible dict.

    This provides backwards compatibility for code expecting TaskResult structure.
    The resulting dict includes the `status` field for discrimination but also
    includes all the fields that TaskResult expects.

    Args:
        outcome: An ActionOutcome instance.

    Returns:
        A dict compatible with both TaskResult and ActionOutcome access patterns.
    """
    base = outcome.model_dump()

    # Ensure backwards-compatible fields are present
    if isinstance(
        outcome, (ActionOutcomeSuccess, ActionOutcomeScatter, ActionOutcomeGather)
    ):
        # These already have result/result_typename from model_dump
        pass
    elif isinstance(outcome, ActionOutcomeError):
        # Add result fields for backwards compat (they're properties, not fields)
        base["result"] = None
        base["result_typename"] = "NoneType"
    elif isinstance(outcome, ActionOutcomeSkipped):
        # Add result and error fields for backwards compat
        base["result"] = None
        base["result_typename"] = "NoneType"
        base["error"] = None
        base["error_typename"] = None

    return base
