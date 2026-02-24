import re
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from tracecat.cases.enums import CaseEventType
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowID, WorkflowIDShort
from tracecat.store import Source

# TODO(deps): This is only supported starting pydantic 2.11+
WorkflowSource = Source[WorkflowID]


_INVALID_GIT_REF_CHARS_RE = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def validate_short_branch_name(value: str, *, field_name: str) -> str:
    """Validate Git-safe short branch names."""
    if value == "":
        raise ValueError(f"{field_name} cannot be empty")
    if value.startswith("refs/"):
        raise ValueError(
            f"{field_name} must be a short branch name, not a full ref (refs/...)"
        )
    if value in {".", "..", "@", "HEAD"}:
        raise ValueError(f"{field_name} must be a valid branch name")
    if value.startswith("/") or value.endswith("/"):
        raise ValueError(f"{field_name} cannot start or end with '/'")
    if value.startswith("-"):
        raise ValueError(f"{field_name} cannot start with '-'")
    if value.endswith("."):
        raise ValueError(f"{field_name} cannot end with '.'")
    if any(part.endswith(".lock") for part in value.split("/")):
        raise ValueError(
            f"{field_name} cannot contain path segments ending with '.lock'"
        )
    if ".." in value:
        raise ValueError(f"{field_name} cannot contain '..'")
    if "//" in value:
        raise ValueError(f"{field_name} cannot contain '//'")
    if "@{" in value:
        raise ValueError(f"{field_name} cannot contain '@{{'")
    if _INVALID_GIT_REF_CHARS_RE.search(value):
        raise ValueError(
            f"{field_name} contains invalid characters for a Git branch name"
        )
    if any(part.startswith(".") or part.endswith(".") for part in value.split("/")):
        raise ValueError(
            f"{field_name} contains invalid path segments for a Git branch name"
        )
    return value


class WorkflowDslPublish(BaseModel):
    message: str | None = None
    branch: str | None = None
    create_pr: bool = False
    pr_base_branch: str | None = None


class WorkflowDslPublishResult(BaseModel):
    status: Literal["committed", "no_op"]
    commit_sha: str | None = None
    branch: str
    base_branch: str
    pr_url: str | None = None
    pr_number: int | None = None
    pr_reused: bool = False
    message: str


class WorkflowSyncPullRequest(BaseModel):
    """Request model for pulling workflows from a Git repository."""

    commit_sha: str = Field(
        ...,
        description="Specific commit SHA to pull from",
        min_length=40,
        max_length=64,
    )

    dry_run: bool = Field(
        default=False,
        description="Validate only, don't perform actual import",
    )


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


class RemoteCaseTrigger(BaseModel):
    """Represents a case trigger configuration in a remote store."""

    status: Status = Field(default="offline")
    event_types: list[CaseEventType] = Field(default_factory=list)
    tag_filters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_event_types(self) -> "RemoteCaseTrigger":
        if self.status == "online" and not self.event_types:
            raise ValueError("event_types must be non-empty when status is online")
        return self


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

    every: timedelta | None = None
    """Interval in seconds for the schedule (ISO 8601 duration string in DB, float seconds here for remote)."""

    offset: timedelta | None = None
    """Offset in seconds before the schedule starts."""

    start_at: datetime | None = None
    """ISO 8601 datetime string for when the schedule starts."""

    end_at: datetime | None = None
    """ISO 8601 datetime string for when the schedule ends."""

    timeout: float | None = None
    """Maximum number of seconds to wait for the workflow to complete."""

    @model_validator(mode="after")
    def validate_spec(self) -> "RemoteWorkflowSchedule":
        if self.cron is None and self.every is None:
            raise ValueError(
                "Either cron or every must be provided for a remote schedule"
            )
        return self


class RemoteWorkflowDefinition(BaseModel):
    """Represents a workflow definition in a remote store.

    This is the format that is used to store workflow definitions in the remote store.
    """

    type: Literal["workflow"] = Field(default="workflow", frozen=True)

    id: WorkflowIDShort
    """Stable short ID of the workflow in the remote store."""

    # version: int // We don't really need version
    alias: str | None = None
    """The alias of the workflow in the remote store."""

    folder_path: str | None = Field(
        default=None,
        description="Folder path in workspace using materialized path format, e.g. '/security/detections/'",
    )
    """Folder path where this workflow should be placed in the workspace."""

    tags: list[RemoteWorkflowTag] | None = None
    """Tags for the workflow."""

    schedules: list[RemoteWorkflowSchedule] | None = None
    """Schedules for the workflow."""

    webhook: RemoteWebhook | None = None
    """Webhook for the workflow."""

    case_trigger: RemoteCaseTrigger | None = None
    """Case trigger configuration for the workflow."""

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
