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
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from tracecat.core.schemas import Schema
from tracecat.identifiers.workflow import AnyWorkflowID
from tracecat.triggers.enums import (
    EventSourceKind,
    SubscriptionStatus,
    TriggerKind,
    TriggerStatus,
    TriggerTargetType,
)

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

    kind: Literal[EventSourceKind.AWS_SNS] = EventSourceKind.AWS_SNS
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
        description="The provider's subscription ID, e.g. the SNS subscription ARN"
    )
    endpoint_url: str = Field(
        description="Point the feed (e.g. the SNS subscription) at this HTTPS URL."
        " It does not change."
    )
    created_at: datetime
    updated_at: datetime


# ----- Triggers -----


class ObjectStorageTriggerConfig(Schema):
    """Fire once for each object created in a source whose key matches."""

    kind: Literal[TriggerKind.OBJECT_STORAGE] = TriggerKind.OBJECT_STORAGE
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


TriggerConfig = Annotated[ObjectStorageTriggerConfig, Field(discriminator="kind")]
TriggerConfigRead = Annotated[
    ObjectStorageTriggerConfigRead, Field(discriminator="kind")
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

    type: Literal[TriggerTargetType.WORKFLOW] = TriggerTargetType.WORKFLOW
    workflow_id: AnyWorkflowID


class AgentBindingTarget(Schema):
    """Start a new session of the agent preset's current version."""

    type: Literal[TriggerTargetType.AGENT] = TriggerTargetType.AGENT
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
