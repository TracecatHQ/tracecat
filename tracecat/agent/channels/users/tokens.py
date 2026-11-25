"""Signed, short-lived link tokens and the provider sign-in state bound to them."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError
from pydantic import BaseModel, ConfigDict, ValidationError

from tracecat.agent.channels.users.enums import ChannelLinkError
from tracecat.agent.channels.users.types import ChannelIdentity
from tracecat.auth.secrets import get_service_key
from tracecat.exceptions import TracecatValidationError

LINK_TOKEN_TTL = timedelta(minutes=10)
_ISSUER = "tracecat-api"
_LINK_TOKEN_AUDIENCE = "tracecat-channel-link"
_SIGN_IN_STATE_AUDIENCE = "tracecat-channel-link-sign-in"
_REQUIRED_CLAIMS = ["iss", "aud", "iat", "exp", "jti"]


class ChannelLinkInvalid(TracecatValidationError):
    """The link token or sign-in state is malformed, expired, used, or mismatched."""

    def __init__(
        self, message: str, *, error: ChannelLinkError = ChannelLinkError.INVALID
    ) -> None:
        super().__init__(message)
        self.error = error


class ChannelLinkClaims(BaseModel):
    """What a link token asserts: one provider user, offered to one organization."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    jti: str
    exp: int
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    bot_id: uuid.UUID
    identity: ChannelIdentity
    interaction_id: str


class _SignInStateClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    link_token: str
    user_id: uuid.UUID


class ChannelLinkSignIn(_SignInStateClaims):
    """A sign-in state: the link token's claims and the Tracecat user who confirmed."""

    claims: ChannelLinkClaims


def _encode(*, audience: str, iat: datetime, claims: dict[str, Any]) -> str:
    payload = {
        "iss": _ISSUER,
        "aud": audience,
        "iat": int(iat.timestamp()),
        **claims,
    }
    return jwt.encode(payload, get_service_key(), algorithm="HS256")


def _decode[ClaimsT: BaseModel](
    token: str, audience: str, claims_type: type[ClaimsT]
) -> ClaimsT:
    try:
        payload = jwt.decode(
            token,
            get_service_key(),
            algorithms=["HS256"],
            audience=audience,
            issuer=_ISSUER,
            options={"require": _REQUIRED_CLAIMS},
        )
        return claims_type.model_validate(payload)
    except (PyJWTError, ValidationError) as exc:
        raise ChannelLinkInvalid("This link is invalid or has expired") from exc


def mint_link_token(
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    bot_id: uuid.UUID,
    identity: ChannelIdentity,
    interaction_id: str,
) -> str:
    """Sign a link token that expires after `LINK_TOKEN_TTL`."""
    now = datetime.now(UTC)
    claims = ChannelLinkClaims(
        jti=secrets.token_urlsafe(16),
        exp=int((now + LINK_TOKEN_TTL).timestamp()),
        organization_id=organization_id,
        workspace_id=workspace_id,
        bot_id=bot_id,
        identity=identity,
        interaction_id=interaction_id,
    )
    return _encode(
        audience=_LINK_TOKEN_AUDIENCE, iat=now, claims=claims.model_dump(mode="json")
    )


def read_link_token(token: str) -> ChannelLinkClaims:
    """Verify a link token's signature and expiry.

    Raises:
        ChannelLinkInvalid: If the token is malformed or expired.
    """
    return _decode(token, _LINK_TOKEN_AUDIENCE, ChannelLinkClaims)


def mint_sign_in_state(*, link_token: str, user_id: uuid.UUID) -> str:
    """Bind a link token to the confirming user; expires with the link token."""
    link = read_link_token(link_token)
    state = _SignInStateClaims(link_token=link_token, user_id=user_id)
    return _encode(
        audience=_SIGN_IN_STATE_AUDIENCE,
        iat=datetime.now(UTC),
        claims={"exp": link.exp, "jti": link.jti, **state.model_dump(mode="json")},
    )


def read_sign_in_state(state: str) -> ChannelLinkSignIn:
    """Verify a sign-in state and the link token inside it.

    Raises:
        ChannelLinkInvalid: If either is malformed or expired.
    """
    signed = _decode(state, _SIGN_IN_STATE_AUDIENCE, _SignInStateClaims)
    return ChannelLinkSignIn(
        link_token=signed.link_token,
        claims=read_link_token(signed.link_token),
        user_id=signed.user_id,
    )
