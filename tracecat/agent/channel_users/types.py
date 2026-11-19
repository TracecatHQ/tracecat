"""Channel sender identity and evidence, as the channel ingress reports them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    """A provider user within one provider tenant (a Slack team, a Teams tenant)."""

    provider: str
    provider_tenant_id: str
    provider_user_id: str


@dataclass(frozen=True, slots=True)
class ChannelSenderEvidence(ChannelIdentity):
    """What the provider says about a sender; email counts only when it qualifies."""

    email: str | None
    email_confirmed: bool
    is_guest: bool
    is_external: bool


@dataclass(frozen=True, slots=True)
class ChannelActor:
    """The Tracecat user behind a sender; `user_id` only for a linked current member."""

    user_id: uuid.UUID | None
    name: str | None
    email: str | None
