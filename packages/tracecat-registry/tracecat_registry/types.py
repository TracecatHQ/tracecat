"""TypedDict definitions for UDF return types.

These types provide static type information for SDK clients and IDE autocomplete.
All dict return types from UDFs are serialized to JSON, so UUIDs become strings
and datetimes become ISO format strings.
"""

from uuid import UUID
from datetime import datetime
from typing import Any, NotRequired, TypedDict


# ============================================================================
# Common Types
# ============================================================================


class UserRead(TypedDict):
    """User information."""

    id: UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    role: str
    first_name: str | None
    last_name: str | None
    settings: dict[str, Any]


# ============================================================================
# Cases Types
# ============================================================================


class CaseTagRead(TypedDict):
    """Tag attached to a case."""

    id: UUID
    name: str
    ref: str
    color: str | None


class CaseFieldRead(TypedDict):
    """Case field with value."""

    id: str  # Field name (e.g., 'id', 'created_at', 'custom_field')
    type: str
    description: str | None
    nullable: bool
    default: Any
    reserved: bool
    value: Any


class CaseDropdownValueRead(TypedDict):
    """Per-case dropdown value with full definition/option info."""

    id: UUID
    definition_id: UUID
    definition_ref: str
    definition_name: str
    option_id: UUID | None
    option_label: str | None
    option_ref: str | None
    option_icon_name: str | None
    option_color: str | None


class Case(TypedDict):
    """Case information returned by create/update/assign operations.

    These operations use case.to_dict() which returns database column values.
    """

    id: UUID
    case_number: int
    summary: str
    description: str
    priority: str
    severity: str
    status: str
    payload: dict[str, Any] | None
    assignee_id: UUID | None
    workspace_id: UUID
    created_at: datetime
    updated_at: datetime


class CaseRead(TypedDict):
    """Case information returned by get_case.

    Uses CaseRead schema with human-readable fields like short_id.
    All UUIDs and datetimes are serialized to strings in JSON output.
    """

    id: UUID
    short_id: str
    summary: str
    description: str
    priority: str
    severity: str
    status: str
    payload: dict[str, Any] | None
    fields: list[CaseFieldRead]
    tags: list[CaseTagRead]
    dropdown_values: NotRequired[list[CaseDropdownValueRead]]
    assignee: UserRead | None
    created_at: datetime
    updated_at: datetime


class CaseReadMinimal(TypedDict):
    """Minimal case information returned by search operations.

    Contains core case fields without description, payload, or custom fields.
    """

    id: UUID
    short_id: str
    summary: str
    priority: str
    severity: str
    status: str
    tags: list[CaseTagRead]
    dropdown_values: NotRequired[list[CaseDropdownValueRead]]
    assignee: UserRead | None
    created_at: datetime
    updated_at: datetime
    num_tasks_completed: int
    num_tasks_total: int


class CaseListResponse(TypedDict):
    """Paginated case list response."""

    items: list[CaseReadMinimal]
    next_cursor: str | None
    prev_cursor: str | None
    has_more: bool
    has_previous: bool
    total_estimate: int | None


class CaseComment(TypedDict):
    """Case comment information."""

    id: UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: UUID | None
    last_edited_at: datetime | None


class CaseCommentRead(TypedDict):
    """Case comment information."""

    id: UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: UUID | None
    user: UserRead | None
    last_edited_at: datetime | None


class CaseTaskRead(TypedDict):
    """Case task information."""

    id: UUID
    created_at: datetime
    updated_at: datetime
    case_id: UUID
    title: str
    description: str | None
    priority: str
    status: str
    assignee: UserRead | None
    workflow_id: str | None
    default_trigger_values: dict[str, Any] | None


class CaseDurationMetric(TypedDict):
    """OTEL-aligned Gauge metric for case durations."""

    timestamp: datetime
    metric_name: str
    value: float
    duration_name: str
    duration_slug: str
    case_priority: str
    case_severity: str
    case_status: str
    case_id: str
    case_short_id: str


class TagRead(TypedDict):
    """Tag information."""

    id: UUID
    name: str
    ref: str
    color: str | None


# ============================================================================
# Case Events Types
# ============================================================================


class FieldDiff(TypedDict):
    """Single field change."""

    field: str
    old: Any
    new: Any


class CreatedEvent(TypedDict, total=False):
    """Event for when a case is created."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "case_created"


class StatusChangedEvent(TypedDict, total=False):
    """Event for when a case status is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "status_changed"
    old: str
    new: str


class PriorityChangedEvent(TypedDict, total=False):
    """Event for when a case priority is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "priority_changed"
    old: str
    new: str


class SeverityChangedEvent(TypedDict, total=False):
    """Event for when a case severity is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "severity_changed"
    old: str
    new: str


class ClosedEvent(TypedDict, total=False):
    """Event for when a case is closed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "case_closed"
    old: str
    new: str


class ReopenedEvent(TypedDict, total=False):
    """Event for when a case is reopened."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "case_reopened"
    old: str
    new: str


class CaseViewedEvent(TypedDict, total=False):
    """Event for when a case is viewed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "case_viewed"


class UpdatedEvent(TypedDict, total=False):
    """Event for when a case is updated."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "case_updated"
    field: str
    old: str | None
    new: str | None


class FieldsChangedEvent(TypedDict, total=False):
    """Event for when case fields are changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "fields_changed"
    changes: list[FieldDiff]


class AssigneeChangedEvent(TypedDict, total=False):
    """Event for when a case assignee is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "assignee_changed"
    old: str | None
    new: str | None


class PayloadChangedEvent(TypedDict, total=False):
    """Event for when a case payload is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "payload_changed"


class AttachmentCreatedEvent(TypedDict, total=False):
    """Event for when an attachment is created."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "attachment_created"
    attachment_id: str
    file_name: str
    content_type: str
    size: int


class AttachmentDeletedEvent(TypedDict, total=False):
    """Event for when an attachment is deleted."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "attachment_deleted"
    attachment_id: str
    file_name: str


class TagAddedEvent(TypedDict, total=False):
    """Event for when a tag is added."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "tag_added"
    tag_id: str
    tag_ref: str
    tag_name: str


class TagRemovedEvent(TypedDict, total=False):
    """Event for when a tag is removed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "tag_removed"
    tag_id: str
    tag_ref: str
    tag_name: str


class TaskCreatedEvent(TypedDict, total=False):
    """Event for when a task is created."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "task_created"
    task_id: str
    title: str


class TaskDeletedEvent(TypedDict, total=False):
    """Event for when a task is deleted."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "task_deleted"
    task_id: str
    title: str | None


class TaskAssigneeChangedEvent(TypedDict, total=False):
    """Event for when a task assignee is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "task_assignee_changed"
    task_id: str
    title: str
    old: str | None
    new: str | None


class TaskStatusChangedEvent(TypedDict, total=False):
    """Event for when a task status is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "task_status_changed"
    task_id: str
    title: str
    old: str
    new: str


class TaskPriorityChangedEvent(TypedDict, total=False):
    """Event for when a task priority is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "task_priority_changed"
    task_id: str
    title: str
    old: str
    new: str


class TaskWorkflowChangedEvent(TypedDict, total=False):
    """Event for when a task workflow is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str  # "task_workflow_changed"
    task_id: str
    title: str
    old: str | None
    new: str | None


class DropdownValueChangedEvent(TypedDict, total=False):
    """Event for when a case dropdown value is changed."""

    user_id: UUID | None
    created_at: str
    wf_exec_id: str | None
    type: str
    definition_id: str
    definition_ref: str
    definition_name: str
    old_option_id: str | None
    old_option_label: str | None
    new_option_id: str | None
    new_option_label: str | None


# Union type for all case events
type CaseEvent = (
    CreatedEvent
    | StatusChangedEvent
    | PriorityChangedEvent
    | SeverityChangedEvent
    | ClosedEvent
    | ReopenedEvent
    | CaseViewedEvent
    | UpdatedEvent
    | FieldsChangedEvent
    | AssigneeChangedEvent
    | PayloadChangedEvent
    | AttachmentCreatedEvent
    | AttachmentDeletedEvent
    | TagAddedEvent
    | TagRemovedEvent
    | TaskCreatedEvent
    | TaskDeletedEvent
    | TaskAssigneeChangedEvent
    | TaskStatusChangedEvent
    | TaskPriorityChangedEvent
    | TaskWorkflowChangedEvent
    | DropdownValueChangedEvent
)


class CaseEventsWithUsers(TypedDict):
    """Case events with associated users."""

    events: list[CaseEvent]
    users: list[UserRead]


# ============================================================================
# Case Attachments Types
# ============================================================================


class CaseAttachmentRead(TypedDict):
    """Case attachment metadata."""

    id: UUID
    case_id: UUID
    file_id: UUID
    file_name: str
    content_type: str
    size: int
    sha256: str
    created_at: datetime
    updated_at: datetime


class CaseAttachmentDownloadData(TypedDict):
    """Case attachment download data."""

    file_name: str
    content_type: str
    content_base64: str


class CaseAttachmentDownloadResponse(TypedDict):
    """Attachment download URL response."""

    id: UUID
    download_url: str
    file_name: str
    content_type: str


# ============================================================================
# Tables Types
# ============================================================================


class TableColumnRead(TypedDict):
    """Table column definition."""

    id: UUID
    name: str
    type: str
    nullable: bool
    default: Any
    is_index: bool
    options: list[str] | None


class Table(TypedDict):
    """Table metadata returned by create_table and list_tables.

    Uses to_dict() which returns database column values.
    """

    id: UUID
    name: str
    workspace_id: UUID
    created_at: datetime
    updated_at: datetime


class TableRead(TypedDict):
    """Table metadata with columns returned by get_table_metadata.

    Uses TableRead schema for structured column information.
    """

    id: UUID
    name: str
    columns: list[TableColumnRead]


class TableSearchResponse(TypedDict):
    """Cursor-paginated table row search response."""

    items: list[dict[str, Any]]
    next_cursor: str | None
    prev_cursor: str | None
    has_more: bool
    has_previous: bool
    total_estimate: NotRequired[int | None]


# ============================================================================
# Agent Types
# ============================================================================


class RunUsage(TypedDict):
    """Agent run usage statistics."""

    requests: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


class AgentOutputRead(TypedDict):
    """Agent execution output returned by run endpoint."""

    output: Any
    message_history: list[dict[str, Any]] | None
    duration: float
    usage: RunUsage | None
    session_id: str  # UUID serialized as string


class AgentPresetRead(TypedDict):
    """Agent preset information."""

    id: UUID
    name: str
    slug: str
    model_name: str
    model_provider: str
    description: str | None
    instructions: str | None
    base_url: str | None
    output_type: Any
    actions: list[str] | None
    created_at: datetime
    updated_at: datetime
