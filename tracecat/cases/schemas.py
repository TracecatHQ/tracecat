from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from pydantic import (
    AfterValidator,
    Field,
    field_validator,
    model_validator,
)

from tracecat import config
from tracecat.auth.schemas import UserRead
from tracecat.cases.agent_invocations.types import CaseCommentAgentInvocationError
from tracecat.cases.constants import RESERVED_CASE_FIELDS
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownValueInput,
    CaseDropdownValueRead,
)
from tracecat.cases.durations.schemas import CaseDurationRead
from tracecat.cases.enums import (
    CaseCommentAgentInvocationStatus,
    CaseFieldKind,
    CaseFieldReadType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
    MentionTargetType,
)
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.core.schemas import Schema
from tracecat.custom_fields.schemas import (
    CustomFieldCreate,
    CustomFieldUpdate,
)
from tracecat.identifiers.workflow import (
    AnyWorkflowID,
    WorkflowIDShort,
    WorkflowUUID,
)
from tracecat.query.aggregations import AggregationSpec
from tracecat.query.filters import Filter
from tracecat.tables.common import parse_postgres_default
from tracecat.tables.enums import SqlType


def _aggregate_datetime_utc(value: datetime) -> datetime:
    """Keep timestamp outputs as UTC instants, independent of the session zone."""
    if value.tzinfo is None:
        raise ValueError("Aggregation timestamps must be timezone-aware")
    return value.astimezone(UTC)


type CaseAggregateValue = (
    str
    | bool
    | int
    | float
    | Decimal
    | uuid.UUID
    | Annotated[datetime, AfterValidator(_aggregate_datetime_utc)]
    | date
    | None
)


class CaseAggregateRequest(AggregationSpec):
    """Filter and aggregate cases in one workspace.

    BIGINT/NUMERIC sums, means, medians, and NUMERIC min/max are widened to
    float8 JSON numbers. NUMERIC group keys remain exact decimal strings.
    TEXT/SELECT group keys use their first 256 characters, so values sharing
    that prefix collapse into one group. Missing values form a null group.

    When grouping by tags, each case appears once in each of its tag groups.
    A case with multiple tags contributes to multiple groups, so adding the
    group counts can exceed the number of matching cases. Untagged cases form
    the null group. Counts, counts of populated fields, and minimum group sizes
    always count each case once per group. Sum, mean, and median are unavailable
    when grouping by tags. Tag filters select cases before grouping; all tags
    on the matching cases remain available as groups.
    """

    filters: Filter | None = Field(default=None)
    limit: int = Field(
        default=config.TRACECAT__LIMIT_AGG_GROUPS_DEFAULT,
        ge=1,
        le=config.TRACECAT__LIMIT_AGG_GROUPS_MAX,
    )


class CaseAggregateResponse(Schema):
    """Flat case aggregation groups and whether more groups exist."""

    # Callers choose output aliases, so fixed field names cannot model a group.
    # Values are restricted to the supported SQL scalar types.
    groups: list[dict[str, CaseAggregateValue]]
    truncated: bool


class CaseRef(Schema):
    """Stable reference to another case recorded in event data."""

    id: uuid.UUID
    short_id: str


class CaseParentRead(CaseRef):
    """Summary of a sub-case's parent case."""

    summary: str


CaseHierarchyFilter = Literal["all", "top_level"]
"""Which cases a list returns: every case, or only cases without a parent."""

type CaseCommentDeleteMode = Literal["soft", "hard"]


class CaseReadMinimal(Schema):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    assignee: UserRead | None = None
    tags: list[CaseTagRead] = Field(default_factory=list)
    dropdown_values: list[CaseDropdownValueRead]
    rows: list[CaseTableRowRead] = Field(default_factory=list)
    durations: list[CaseDurationRead] | None = None
    field_values: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    num_tasks_completed: int = Field(default=0)
    num_tasks_total: int = Field(default=0)
    parent: CaseParentRead | None = None
    num_sub_cases: int


class CaseStatusGroupCounts(Schema):
    new: int = 0
    in_progress: int = 0
    on_hold: int = 0
    resolved: int = 0
    closed: int = 0
    unknown: int = 0
    other: int = 0


class CaseSearchAggregateRead(Schema):
    total: int
    status_groups: CaseStatusGroupCounts


class CaseRead(Schema):
    id: uuid.UUID
    short_id: str
    created_at: datetime
    updated_at: datetime
    summary: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    description: str
    fields: list[CaseFieldRead]
    assignee: UserRead | None = None
    payload: dict[str, Any] | None
    tags: list[CaseTagRead] = Field(default_factory=list)
    dropdown_values: list[CaseDropdownValueRead]
    rows: list[CaseTableRowRead] = Field(default_factory=list)
    parent: CaseParentRead | None = None
    num_sub_cases: int


class CaseCreate(Schema):
    summary: str
    description: str
    status: CaseStatus
    priority: CasePriority
    severity: CaseSeverity
    fields: dict[str, Any] | None = None
    # Write payload field for persisted per-case dropdown selections.
    # Search filters use the `dropdown` query parameter in routes.
    dropdown_values: list[CaseDropdownValueInput] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None
    parent_id: uuid.UUID | None = Field(
        default=None,
        description="Create the case as a sub-case of this top-level case.",
    )


class CaseUpdate(Schema):
    summary: str | None = None
    description: str | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    severity: CaseSeverity | None = None
    fields: dict[str, Any] | None = None
    # Same persisted write payload shape as create; values here set/clear
    # dropdown selections for this case.
    dropdown_values: list[CaseDropdownValueInput] | None = None
    assignee_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None


class CaseBatchUpdate(Schema):
    """Request body for updating multiple cases."""

    case_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=1000)
    update: CaseUpdate


class CaseBatchDelete(Schema):
    """Request body for deleting multiple cases."""

    case_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=1000)


class CaseBatchSetParent(Schema):
    """Request body for grouping cases as sub-cases of a parent case."""

    case_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=1000)
    parent_id: uuid.UUID


class CaseBatchClearParent(Schema):
    """Request body for removing cases from their parent case."""

    case_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=1000)


class CaseBatchItemResult(Schema):
    """Result of a batch operation for one case."""

    case_id: uuid.UUID
    success: bool
    error: str | None = None


class CaseBatchResponse(Schema):
    """Per-case results and aggregate counts for a batch operation."""

    results: list[CaseBatchItemResult]
    succeeded: int
    failed: int


# Case Fields


def _normalize_case_field_read_type(raw_type: Any) -> CaseFieldReadType:
    if isinstance(raw_type, CaseFieldReadType):
        return raw_type
    if isinstance(raw_type, SqlType):
        return CaseFieldReadType(raw_type.value)

    type_str: str
    if isinstance(raw_type, str):
        type_str = raw_type.upper()
    else:
        type_str = str(raw_type).upper()
        if hasattr(raw_type, "timezone"):
            type_str = (
                "TIMESTAMP WITH TIME ZONE"
                if getattr(raw_type, "timezone", False)
                else "TIMESTAMP WITHOUT TIME ZONE"
            )

    if type_str == "BIGINT":
        return CaseFieldReadType.INTEGER
    if type_str == "TIMESTAMP WITH TIME ZONE":
        return CaseFieldReadType.TIMESTAMPTZ
    return CaseFieldReadType(type_str)


class CaseFieldReadMinimal(Schema):
    """Minimal read model for a case field."""

    id: str
    display_name: str
    type: CaseFieldReadType
    description: str
    nullable: bool
    default: str | None
    reserved: bool
    options: list[str] | None = None
    kind: CaseFieldKind | None = Field(default=None)
    required_on_closure: bool = Field(default=False)

    @classmethod
    def from_sa(
        cls,
        column: sa.engine.interfaces.ReflectedColumn,
        *,
        field_schema: dict[str, Any] | None = None,
    ) -> CaseFieldReadMinimal:
        """Create a CaseFieldReadMinimal from a SQLAlchemy reflected column.

        Args:
            column: The reflected column metadata from SQLAlchemy.
            field_schema: Optional schema metadata for the field.

        Returns:
            A CaseFieldReadMinimal instance populated from the column data.
        """
        kind: CaseFieldKind | None = None
        required_on_closure = False
        options: list[str] | None = None
        display_name = column["name"]
        if field_schema and (meta := field_schema.get(column["name"])):
            read_type = CaseFieldReadType(meta["type"])
            display_name = meta.get("display_name") or column["name"]
            options = meta.get("options")
            if kind_str := meta.get("kind"):
                kind = CaseFieldKind(kind_str)
            if meta.get("required_on_closure"):
                required_on_closure = True
        else:
            read_type = _normalize_case_field_read_type(column["type"])
        return cls.model_validate(
            {
                "id": column["name"],
                "display_name": display_name,
                "type": read_type,
                "description": column.get("comment") or "",
                "nullable": column["nullable"],
                "default": parse_postgres_default(column.get("default")),
                "reserved": column["name"] in RESERVED_CASE_FIELDS,
                "options": options,
                "kind": kind,
                "required_on_closure": required_on_closure,
            }
        )


class CaseFieldCreate(CustomFieldCreate):
    """Create a new case field."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: CaseFieldKind | None = Field(default=None)
    required_on_closure: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_kind_type_pair(self) -> CaseFieldCreate:
        """Validate the semantic kind against the storage type."""
        if self.kind is None:
            return self

        if self.kind is CaseFieldKind.LONG_TEXT and self.type is not SqlType.TEXT:
            raise ValueError("Case field kind LONG_TEXT requires type TEXT")
        if self.kind is CaseFieldKind.URL and self.type is not SqlType.JSONB:
            raise ValueError("Case field kind URL requires type JSONB")
        return self


class CaseFieldUpdate(CustomFieldUpdate):
    """Update a case field."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    required_on_closure: bool | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def reject_kind_updates(cls, data: Any) -> Any:
        """Reject create-only kind updates."""
        if isinstance(data, dict) and "kind" in data:
            raise ValueError("Case field kind can only be set when creating a field")
        return data


class CaseFieldRead(CaseFieldReadMinimal):
    """Read model for a case field."""

    value: Any


# Case Comments


class CaseCommentWorkflowStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CaseCommentWorkflowRead(Schema):
    workflow_id: uuid.UUID | None = None
    title: str
    alias: str | None = None
    wf_exec_id: str | None = None
    status: CaseCommentWorkflowStatus


class CaseCommentAgentInvocationRead(Schema):
    """Read model for an agent invocation triggered by a comment mention."""

    id: uuid.UUID
    preset_name: str
    preset_slug: str
    status: CaseCommentAgentInvocationStatus
    session_id: uuid.UUID | None = None
    error: CaseCommentAgentInvocationError | None = None


class CaseCommentAgentAttributionRead(Schema):
    """Read model for agent attribution on a generated comment reply."""

    invocation_id: uuid.UUID
    preset_name: str
    preset_slug: str
    session_id: uuid.UUID | None = None


class CaseCommentMentionRead(Schema):
    id: uuid.UUID
    target_type: MentionTargetType
    target_id: uuid.UUID
    label: str
    created_at: datetime
    invocation: CaseCommentAgentInvocationRead | None = None


class CaseCommentRead(Schema):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: uuid.UUID | None = None
    workflow: CaseCommentWorkflowRead | None = None
    agent: CaseCommentAgentAttributionRead | None = None
    user: UserRead | None = None
    last_edited_at: datetime | None = None
    deleted_at: datetime | None = None
    is_deleted: bool = Field(default=False)
    mentions: list[CaseCommentMentionRead] = Field(default_factory=list)


class CaseCommentThreadRead(Schema):
    comment: CaseCommentRead
    replies: list[CaseCommentRead] = Field(default_factory=list)
    reply_count: int = Field(default=0)
    last_activity_at: datetime


CASE_COMMENT_MAX_LENGTH = 25_000


class CaseCommentCreate(Schema):
    content: str = Field(default=..., max_length=CASE_COMMENT_MAX_LENGTH)
    parent_id: uuid.UUID | None = Field(default=None)
    workflow_id: AnyWorkflowID | None = Field(default=None)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        return value.replace("\x00", "").strip()

    @model_validator(mode="after")
    def validate_content(self) -> CaseCommentCreate:
        """Allow an empty body only when the comment runs a workflow."""
        if not self.content and self.workflow_id is None:
            raise ValueError("Comment content cannot be blank")
        return self


class CaseCommentUpdate(Schema):
    content: str | None = Field(
        default=None, min_length=1, max_length=CASE_COMMENT_MAX_LENGTH
    )
    parent_id: uuid.UUID | None = Field(default=None)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | None) -> str | None:
        """Reject blank edits; only creation with a workflow may leave a comment empty."""
        if value is None:
            return None
        stripped = value.replace("\x00", "").strip()
        if not stripped:
            raise ValueError("Comment content cannot be blank")
        return stripped


# Case Tasks


class CaseTaskRead(Schema):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    case_id: uuid.UUID
    title: str
    description: str | None
    priority: CasePriority
    status: CaseTaskStatus
    assignee: UserRead | None = None
    workflow_id: WorkflowIDShort | None
    default_trigger_values: dict[str, Any] | None = None

    @field_validator("workflow_id", mode="before")
    @classmethod
    def convert_workflow_id(cls, v: AnyWorkflowID | None) -> WorkflowIDShort | None:
        """Convert any workflow ID format to short form."""
        if v is None:
            return None
        return WorkflowUUID.new(v).short()


class CaseTaskCreate(Schema):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    priority: CasePriority = Field(default=CasePriority.UNKNOWN)
    status: CaseTaskStatus = Field(default=CaseTaskStatus.TODO)
    assignee_id: uuid.UUID | None = Field(default=None)
    workflow_id: AnyWorkflowID | None = Field(default=None)
    default_trigger_values: dict[str, Any] | None = Field(default=None)


class CaseTaskUpdate(Schema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    priority: CasePriority | None = Field(default=None)
    status: CaseTaskStatus | None = Field(default=None)
    assignee_id: uuid.UUID | None = Field(default=None)
    workflow_id: AnyWorkflowID | None = Field(default=None)
    default_trigger_values: dict[str, Any] | None = Field(default=None)


class Change[OldType: Any, NewType: Any](Schema):
    field: str
    old: OldType
    new: NewType


# Internal


class InternalCaseData(Schema):
    """Case data matching the Case SQLAlchemy model's to_dict() output.

    This is the raw database representation used by UDFs for create/update operations.
    """

    id: uuid.UUID
    case_number: int
    summary: str
    description: str
    priority: CasePriority
    severity: CaseSeverity
    status: CaseStatus
    payload: dict[str, Any] | None
    assignee_id: uuid.UUID | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class InternalCaseCommentData(Schema):
    """Comment data matching the CaseComment SQLAlchemy model's to_dict() output.

    This is the raw database representation used by UDFs for create/update operations.
    """

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    content: str
    parent_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None
    workflow_title: str | None = None
    workflow_alias: str | None = None
    workflow_wf_exec_id: str | None = None
    workflow_status: CaseCommentWorkflowStatus | None = None
    case_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None = None
    last_edited_at: datetime | None = None
    deleted_at: datetime | None = None
