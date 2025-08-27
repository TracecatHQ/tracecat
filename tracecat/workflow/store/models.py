from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowID, WorkflowIDShort
from tracecat.store import Source

# TODO(deps): This is only supported starting pydantic 2.11+
WorkflowSource = Source[WorkflowID]


class WorkflowDslPublish(BaseModel):
    message: str | None = None


class RemoteRegistry(BaseModel):
    """Represents a remote registry."""

    base_version: str
    """Base Tracecat registry version. This should be a semver tag (tied to the Tracecat version)."""

    repositories: list[str] | None = None
    """List of Git repository URLs for Tracecat custom registries with `ref` (commit hash).

    Example:
    ```
    - "git+ssh://git@github.com/TracecatHQ/custom-registry.git#<commit-hash>"
    ```
    """

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Dump the model as a dictionary."""
        kwargs.update(exclude_none=True, exclude_unset=True, mode="json")
        return super().model_dump(*args, **kwargs)


_SER_DSL_KEY_ORDER = (
    "title",
    "description",
    "entrypoint",
    "config",
    "triggers",
    "inputs",
    "error_handler",
    "actions",
    "returns",
)
"""Defines the preferred order of keys for serializing DSLInput objects.
This order is based on the fields present in the DSLInput type definition."""


class RemoteWorkflowTag(BaseModel):
    """Represents a tag for a workflow in a remote store."""

    name: str
    """The name of the tag."""


Status = Literal["online", "offline"]


class RemoteWebhook(BaseModel):
    """Represents a webhook for a workflow in a remote store."""

    methods: list[str]
    """The methods of the webhook."""
    status: Status = Field(default="online")
    """Status of the webhook, either 'online' or 'offline'."""


class RemoteWorkflowSchedule(BaseModel):
    """
    Represents a schedule for a workflow in a remote store.

    This model mirrors the Schedule resource in the database schema,
    but is intended for use in remote workflow definitions.
    """

    model_config = ConfigDict(extra="ignore")

    status: Status = Field(default="online")
    """Status of the schedule, either 'online' or 'offline'."""

    cron: str | None = None
    """Cron expression for the schedule, if applicable."""

    every: float | None = Field(default=None, ge=0)
    """Interval in seconds for the schedule (ISO 8601 duration string in DB, float seconds here for remote)."""

    offset: float | None = Field(default=None, ge=0)
    """Offset in seconds before the schedule starts."""

    start_at: str | None = None
    """ISO 8601 datetime string for when the schedule starts."""

    end_at: str | None = None
    """ISO 8601 datetime string for when the schedule ends."""

    timeout: float | None = Field(default=None, ge=0)
    """Maximum number of seconds to wait for the workflow to complete."""


class RemoteWorkflowDefinition(BaseModel):
    """Represents a workflow definition in a remote store.

    This is the format that is used to store workflow definitions in the remote store.
    """

    type: Literal["workflow"] = Field(default="workflow", frozen=True)

    id: WorkflowIDShort
    """Stable short ID of the workflow in the remote store."""

    registry: RemoteRegistry
    """Action registry dependencies required for this workflow."""

    # version: int // We don't really need version
    alias: str | None = None
    """The alias of the workflow in the remote store."""

    tags: list[RemoteWorkflowTag] | None = None
    """Tags for the workflow."""

    schedules: list[RemoteWorkflowSchedule] | None = None
    """Schedules for the workflow."""

    webhook: RemoteWebhook | None = None
    """Webhook for the workflow."""

    definition: DSLInput

    @field_serializer("definition", when_used="json")
    def serialize_definition(self, definition: DSLInput, _info: Any) -> dict[str, Any]:
        data = definition.model_dump(exclude_none=True, exclude_unset=True, mode="json")
        # Dict insertion order is guaranteed in Python 3.7+
        ordered: dict[str, Any] = {}
        for key in _SER_DSL_KEY_ORDER:
            if key in data:
                ordered[key] = data.pop(key)
        # Add the rest of the data
        ordered.update(data)
        return ordered


class RemoteStoreManifest(BaseModel):
    """Represents a manifest of a remote repository."""

    version: str
    """The schema version of the remote repository."""
