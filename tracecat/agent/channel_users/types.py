"""Channel sender identity, as the channel ingress reports it."""

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
class ProviderSignIn:
    """The provider user who signed in, with the email the provider reported.

    Wraps `ChannelIdentity` so identity equality stays exact."""

    identity: ChannelIdentity
    email: str | None


@dataclass(frozen=True, slots=True)
class ChannelActor:
    """The Tracecat user behind a sender; `user_id` only for a linked current member."""

    user_id: uuid.UUID | None
    name: str | None
    email: str | None
