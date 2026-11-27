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

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from tracecat.core.schemas import Schema
from tracecat.identifiers.schedules import ScheduleUUID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowID
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


class AwsSnsSourceConfig(Schema):
    """Events pushed by a customer-owned SNS topic, pinned by its ARN."""

    kind: Literal[EventSourceKind.AWS_SNS]
    topic_arn: TopicArn


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
    change. The API key is managed on the workflow's webhook only.
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


TriggerConfig = Annotated[
    ObjectStorageTriggerConfig | ScheduleTriggerConfig | WebhookTriggerConfig,
    Field(discriminator="kind"),
]
TriggerConfigRead = Annotated[
    ObjectStorageTriggerConfigRead
    | ScheduleTriggerConfigRead
    | WebhookTriggerConfigRead,
    Field(discriminator="kind"),
]


class TriggerCreate(Schema):
    name: Name
    config: TriggerConfig
    status: TriggerStatus = Field(default=TriggerStatus.OFFLINE)


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
