"""API models for event sources, triggers and trigger bindings.

Sources and triggers each have a ``config`` that is a union discriminated on
``kind``, one model per kind. The source kind is how events arrive (``aws_sns``);
the trigger kind is what events it matches (``object_storage``).

A trigger config has an input model (create and update) and a read model,
each in its own union. The read model may add display fields such as the
source name.

To add a source kind: an ``EventSourceKind`` value, a config model and union
member here, its ingest path in ``tracecat.triggers.types``, and the trigger
kinds that can read it in ``SOURCE_KINDS_BY_TRIGGER_KIND``. To add a trigger
kind: a ``TriggerKind`` value, an input and a read model and their union
members here, a detail table if it has settings, its source kinds in
``SOURCE_KINDS_BY_TRIGGER_KIND``, and its cases in
``tracecat.triggers.service`` (writing and reading its detail rows).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from tracecat.cases.enums import CaseEventType
from tracecat.core.schemas import Schema
from tracecat.identifiers.schedules import ScheduleUUID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowID
from tracecat.pagination import CursorPaginatedResponse
from tracecat.tiers.enums import Entitlement
from tracecat.triggers.enums import (
    EventSourceKind,
    SubscriptionStatus,
    TriggerKind,
    TriggerStatus,
    TriggerTargetType,
)
from tracecat.webhooks.schemas import (
    WebhookApiKeyRead,
    WebhookMethod,
    normalize_cidrs,
)
from tracecat.workflow.case_triggers.schemas import (
    normalize_event_types,
    normalize_tag_filters,
)
from tracecat.workflow.schedules.schemas import check_cron, check_schedule_spec

# Commercial, GovCloud and China partitions. FIFO topics cannot deliver to
# HTTPS endpoints, so the name allows no ".fifo" suffix.
SNS_TOPIC_ARN_PATTERN = (
    r"^arn:aws(-cn|-us-gov)?:sns:[a-z]{2}(-[a-z]+)+-\d:\d{12}:[A-Za-z0-9_-]{1,256}$"
)

TopicArn = Annotated[
    str,
    # The pattern bounds every part, the topic name included (256 characters).
    StringConstraints(pattern=SNS_TOPIC_ARN_PATTERN),
    Field(
        description="ARN of the customer's SNS topic. The source accepts no other.",
        examples=["arn:aws:sns:us-east-1:123456789012:bucket-events"],
    ),
]
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
Instruction = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
    Field(
        description="What the agent should do with each event. The event itself"
        " is added to the first message as data, never as instructions."
    ),
]


# ----- Event sources -----


# General purpose bucket names. Older buckets in us-east-1 may break these
# rules; they cannot be pinned.
_S3_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_IP_ADDRESS = re.compile(r"\d{1,3}(\.\d{1,3}){3}")
# Prefixes and suffixes S3 reserves for other resource types.
_S3_RESERVED_PREFIXES = ("xn--", "sthree-", "amzn-s3-demo-")
_S3_RESERVED_SUFFIXES = ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")
_S3_BUCKET_ARN = re.compile(
    r"arn:(?P<partition>aws(?:-cn|-us-gov)?):s3:::(?P<name>[^/:]+)"
)


def _normalize_s3_bucket(value: str, *, topic_arn: str | None) -> str | None:
    """Reduce a bucket pin to the bucket name; blank means no pin.

    Accepts a bucket name or a bucket ARN (``arn:aws:s3:::name``). Bucket
    names are unique per partition, and S3 publishes only to a topic in its
    own region, so the topic's partition is the bucket's: an ARN in another
    partition is refused rather than saved as a name that means another
    bucket.

    Args:
        value: The bucket name or ARN as given.
        topic_arn: The source's validated topic ARN, or None if it is invalid
            (its own error is reported then).

    Raises:
        ValueError: If the value is neither, or the ARN's partition is not
            the topic's.
    """
    name = value.strip()
    if not name:
        return None
    if name.startswith("arn:"):
        if (arn := _S3_BUCKET_ARN.fullmatch(name)) is None:
            raise ValueError("Not an S3 bucket ARN (arn:aws:s3:::bucket-name)")
        if topic_arn is not None and arn.group("partition") != topic_arn.split(":")[1]:
            raise ValueError(
                "The bucket ARN is in another AWS partition than the topic"
            )
        name = arn.group("name")
    if (
        not _S3_BUCKET_NAME.fullmatch(name)
        or ".." in name
        or _IP_ADDRESS.fullmatch(name)
        or name.startswith(_S3_RESERVED_PREFIXES)
        or name.endswith(_S3_RESERVED_SUFFIXES)
    ):
        raise ValueError(
            "Not an S3 bucket name: 3 to 63 lowercase letters, digits, dots or"
            " hyphens, starting and ending with a letter or digit, not shaped"
            " like an IP address, and without a prefix or suffix S3 reserves"
        )
    return name


class AwsSnsSourceConfig(Schema):
    """Events pushed by a customer-owned SNS topic, pinned by its ARN."""

    kind: Literal[EventSourceKind.AWS_SNS]
    topic_arn: TopicArn
    bucket: str | None = Field(
        default=None,
        description="Accept events for this S3 bucket only. A bucket name or"
        " ARN (in the topic's partition), saved as the name. Null or omitted"
        " accepts every bucket that publishes to the topic. It checks the"
        " bucket named in each message, so it adds to the topic policy's"
        " `aws:SourceArn` and `aws:SourceAccount` conditions and does not"
        " replace them.",
        examples=["my-bucket"],
    )

    @field_validator("bucket", mode="before")
    @classmethod
    def _normalize_bucket(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        return _normalize_s3_bucket(value, topic_arn=info.data.get("topic_arn"))


EventSourceConfig = Annotated[AwsSnsSourceConfig, Field(discriminator="kind")]


class EventSourceCreate(Schema):
    name: Name
    config: EventSourceConfig
    status: TriggerStatus = Field(default=TriggerStatus.OFFLINE)


class EventSourceUpdate(Schema):
    name: Name | None = None
    config: EventSourceConfig | None = Field(
        default=None,
        description="Replaces the whole config. The kind cannot change. Changing"
        " where events come from resets the subscription to pending.",
    )
    status: TriggerStatus | None = None


class EventSourceRead(Schema):
    id: uuid.UUID
    kind: EventSourceKind
    name: str
    config: EventSourceConfig
    status: TriggerStatus
    subscription_status: SubscriptionStatus
    subscription_ref: str | None = Field(
        description="The provider's subscription ID, e.g. the SNS subscription ARN."
        " Null for callers without trigger:update."
    )
    endpoint_url: str | None = Field(
        description="Point the feed (e.g. the SNS subscription) at this HTTPS URL."
        " It does not change. Null for callers without trigger:update."
    )
    created_at: datetime
    updated_at: datetime


# ----- Triggers -----


class ObjectStorageTriggerConfig(Schema):
    """Fire once for each object created in a source whose key matches."""

    kind: Literal[TriggerKind.OBJECT_STORAGE]
    source_id: uuid.UUID = Field(
        description="The event source to read object events from"
    )
    key_prefix: str = Field(
        default="",
        max_length=1024,
        description="Only keys starting with this fire. Empty matches every key.",
    )


class ObjectStorageTriggerConfigRead(ObjectStorageTriggerConfig):
    source_name: str = Field(description="The event source's display name")


class _ScheduleSettings(Schema):
    """When a schedule runs and with what, as on ``/schedules``.

    Validation lives on the input model only: a read shows the row as
    ``/schedules`` left it (an update there can leave both cron and interval
    set), and one odd row must not fail a whole list.
    """

    cron: str | None = Field(
        default=None, description="A cron expression. Set this or `every`."
    )
    every: timedelta | None = Field(
        default=None, description="ISO 8601 interval. Set this or `cron`."
    )
    offset: timedelta | None = Field(
        default=None,
        description="ISO 8601 delay before the first interval run. Interval only.",
    )
    start_at: datetime | None = Field(
        default=None, description="ISO 8601 datetime before which it does not run"
    )
    end_at: datetime | None = Field(
        default=None, description="ISO 8601 datetime after which it stops"
    )
    timeout: float = Field(
        default=0,
        ge=0,
        description="Most seconds each run may take. 0 means no timeout.",
    )
    inputs: dict[str, Any] | None = Field(
        default=None, description="The workflow's trigger inputs for each run"
    )


class ScheduleTriggerConfig(_ScheduleSettings):
    """Run a workflow on a cron expression or a fixed interval.

    The settings of the workflow's schedule (``/schedules``), which the
    trigger shows and edits. Until schedules start their targets through
    bindings, the trigger's one target is this workflow and cannot change.
    """

    kind: Literal[TriggerKind.SCHEDULE]
    workflow_id: AnyWorkflowID = Field(
        description="The workflow the schedule runs. Fixed after create."
    )

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, cron: str | None) -> str | None:
        # A blank cron is no cron, so the one-spec check below catches it.
        if cron is not None and not cron.strip():
            return None
        return check_cron(cron)

    @model_validator(mode="after")
    def _one_spec(self) -> Self:
        check_schedule_spec(self.cron, self.every)
        return self


class ScheduleTriggerConfigRead(_ScheduleSettings):
    kind: Literal[TriggerKind.SCHEDULE]
    workflow_id: WorkflowID = Field(description="The workflow the schedule runs")
    schedule_id: ScheduleUUID = Field(
        description="The schedule's ID in `/schedules`, which does not change"
    )


class WebhookTriggerConfig(Schema):
    """Run a workflow for each request to its webhook URL.

    The settings of the workflow's webhook (``/workflows/{id}/webhook``),
    which the trigger shows and edits. Every workflow has one webhook; it
    becomes a trigger when it is turned on. Until webhooks start their targets
    through bindings, the trigger's one target is this workflow and cannot
    change. The API key is managed on the workflow's webhook only. Settings
    left out of a create or update keep the webhook's saved values.
    """

    kind: Literal[TriggerKind.WEBHOOK]
    workflow_id: AnyWorkflowID = Field(
        description="The workflow whose webhook this is. Fixed after create."
    )
    methods: list[WebhookMethod] = Field(
        default_factory=lambda: ["POST"],
        min_length=1,
        description="HTTP methods the webhook accepts",
    )
    allowlisted_cidrs: list[str] = Field(
        default_factory=list,
        description="IPv4 addresses or CIDR ranges allowed to call the webhook."
        " Empty allows every address.",
    )
    include_headers: bool = Field(
        default=False,
        description="Pass request headers and metadata to the workflow, not"
        " only the body",
    )

    @field_validator("allowlisted_cidrs")
    @classmethod
    def _valid_cidrs(cls, cidrs: list[str]) -> list[str]:
        return normalize_cidrs(cidrs)


class WebhookTriggerConfigRead(Schema):
    """The webhook as ``/workflows/{id}/webhook`` shows it.

    Readable only with ``workflow:read``, the scope that shows the same URL on
    the workflow page.
    """

    kind: Literal[TriggerKind.WEBHOOK]
    workflow_id: WorkflowID = Field(description="The workflow the webhook runs")
    webhook_id: uuid.UUID = Field(description="The webhook's ID, which does not change")
    methods: list[str] = Field(description="HTTP methods the webhook accepts")
    allowlisted_cidrs: list[str] = Field(
        description="Addresses allowed to call the webhook. Empty allows all."
    )
    include_headers: bool
    url: str = Field(description="The webhook URL. It contains the webhook's secret.")
    api_key: WebhookApiKeyRead | None = Field(
        description="The webhook's API key, if it has one (preview only)"
    )


class CaseEventTriggerConfig(Schema):
    """Run a workflow for each case event of the chosen types.

    The settings of the workflow's case trigger
    (``/workflows/{id}/case-trigger``), which the trigger shows and edits.
    Every workflow has one case trigger; it becomes a trigger when it is
    turned on, or when it is created as one here (it may stay offline).
    Deleting the trigger turns the case trigger off and keeps it. Until case triggers start their targets through bindings, the
    trigger's one target is this workflow and cannot change. Settings left out
    of a create or update keep the case trigger's saved values. Needs the
    ``case_addons`` plan feature.
    """

    kind: Literal[TriggerKind.CASE_TRIGGER]
    workflow_id: AnyWorkflowID = Field(
        description="The workflow whose case trigger this is. Fixed after create."
    )
    event_types: list[CaseEventType] = Field(
        default_factory=list,
        description="Case event types that run the workflow. Must not be empty"
        " while the trigger is online.",
    )
    tag_filters: list[str] = Field(
        default_factory=list,
        description="Case tag refs. When set, only events on cases with one of"
        " these tags run the workflow. Empty matches every case.",
    )

    @field_validator("event_types")
    @classmethod
    def _dedupe_event_types(cls, value: list[CaseEventType]) -> list[CaseEventType]:
        return normalize_event_types(value)

    @field_validator("tag_filters")
    @classmethod
    def _normalize_tag_filters(cls, value: list[str]) -> list[str]:
        return normalize_tag_filters(value)


class CaseEventTriggerConfigRead(Schema):
    """The case trigger as ``/workflows/{id}/case-trigger`` shows it.

    Readable only with ``workflow:read`` and the ``case_addons`` plan
    feature, as on the workflow page.
    """

    kind: Literal[TriggerKind.CASE_TRIGGER]
    workflow_id: WorkflowID = Field(description="The workflow the case trigger runs")
    case_trigger_id: uuid.UUID = Field(
        description="The case trigger's ID, which does not change"
    )
    event_types: list[CaseEventType] = Field(
        description="Case event types that run the workflow"
    )
    tag_filters: list[str] = Field(
        description="Case tag refs that limit which cases match. Empty matches all."
    )


TriggerConfig = Annotated[
    ObjectStorageTriggerConfig
    | ScheduleTriggerConfig
    | WebhookTriggerConfig
    | CaseEventTriggerConfig,
    Field(discriminator="kind"),
]
TriggerConfigRead = Annotated[
    ObjectStorageTriggerConfigRead
    | ScheduleTriggerConfigRead
    | WebhookTriggerConfigRead
    | CaseEventTriggerConfigRead,
    Field(discriminator="kind"),
]


class TriggerCreate(Schema):
    name: Name
    config: TriggerConfig
    status: TriggerStatus = Field(
        default=TriggerStatus.OFFLINE,
        description="Offline when omitted, except for a webhook or a case"
        " trigger: it already exists and keeps its current status unless this"
        " is given.",
    )


class TriggerUpdate(Schema):
    name: Name | None = None
    config: TriggerConfig | None = Field(
        default=None,
        description="Replaces the whole config. Its kind must be the trigger's"
        " kind, which never changes after create.",
    )
    status: TriggerStatus | None = None


class TriggerBindingRead(Schema):
    id: uuid.UUID
    trigger_id: uuid.UUID
    target_type: TriggerTargetType
    target_id: uuid.UUID = Field(description="The workflow or agent preset ID")
    target_name: str = Field(description="The workflow title or agent preset name")
    instruction: str | None
    status: TriggerStatus
    created_at: datetime
    updated_at: datetime


class TriggerRead(Schema):
    id: uuid.UUID
    kind: TriggerKind
    name: str
    status: TriggerStatus
    config: TriggerConfigRead
    bindings: list[TriggerBindingRead]
    created_at: datetime
    updated_at: datetime


class LockedTriggerCount(Schema):
    """Triggers of one kind that a list leaves out because the plan lacks
    the kind's feature."""

    kind: TriggerKind
    entitlement: Entitlement = Field(description="The plan feature the kind needs")
    count: int = Field(description="How many such triggers match the list's filters")


class TriggerListResponse(CursorPaginatedResponse[TriggerRead]):
    locked: list[LockedTriggerCount] = Field(
        default_factory=list,
        description="Triggers matching the filters that are not listed because"
        " the plan lacks their kind's feature, counted per kind. They are kept"
        " and come back when the plan has the feature again.",
    )


# ----- Bindings -----


class WorkflowBindingTarget(Schema):
    """Start the workflow's published definition with the event as its input."""

    type: Literal[TriggerTargetType.WORKFLOW]
    workflow_id: AnyWorkflowID


class AgentBindingTarget(Schema):
    """Start a new session of the agent preset's current version."""

    type: Literal[TriggerTargetType.AGENT]
    agent_preset_id: uuid.UUID
    instruction: Instruction


BindingTarget = Annotated[
    WorkflowBindingTarget | AgentBindingTarget, Field(discriminator="type")
]


class TriggerBindingCreate(Schema):
    target: BindingTarget
    status: TriggerStatus = Field(default=TriggerStatus.ONLINE)


class TriggerBindingUpdate(Schema):
    status: TriggerStatus | None = None
    instruction: Instruction | None = Field(
        default=None, description="Agent bindings only."
    )
