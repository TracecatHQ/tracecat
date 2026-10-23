"""Relying party configuration and challenge state for WebAuthn ceremonies."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import orjson
from fido2.server import Fido2Server
from fido2.webauthn import (
    AttestationConveyancePreference,
    PublicKeyCredentialRpEntity,
)

from tracecat import config
from tracecat.auth.mfa.constants import CHALLENGE_TIMEOUT_MS, CHALLENGE_TTL_SECONDS
from tracecat.redis.client import get_redis_client

ChallengePurpose = Literal["register", "authenticate"]

# ES256, EdDSA, RS256: security keys, platform authenticators, password managers.
ALLOWED_COSE_ALGORITHMS: tuple[int, ...] = (-7, -8, -257)


@dataclass(frozen=True, slots=True)
class RelyingParty:
    id: str
    name: str
    origin: str


class WebAuthnUnavailableError(Exception):
    """WebAuthn cannot be used with the configured public app URL."""


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def get_relying_party() -> RelyingParty:
    """Derive the relying party from ``TRACECAT__PUBLIC_APP_URL``.

    Credentials are bound to this host, so changing the public app domain
    invalidates every registered security key.

    Raises:
        WebAuthnUnavailableError: The URL is not HTTPS (or localhost) on a
            domain name, which browsers require for WebAuthn.
    """
    parts = urlsplit(config.TRACECAT__PUBLIC_APP_URL)
    host = (parts.hostname or "").lower()
    if not host:
        raise WebAuthnUnavailableError("TRACECAT__PUBLIC_APP_URL has no hostname.")
    if _is_ip_address(host):
        raise WebAuthnUnavailableError(
            "Security keys require Tracecat to be served from a domain name, "
            "not an IP address."
        )
    if parts.scheme != "https" and host != "localhost":
        raise WebAuthnUnavailableError(
            "Security keys require Tracecat to be served over HTTPS."
        )
    # Browsers omit default ports from the origin in clientDataJSON.
    port = parts.port
    if port is None or (parts.scheme, port) in {("http", 80), ("https", 443)}:
        origin = f"{parts.scheme}://{host}"
    else:
        origin = f"{parts.scheme}://{host}:{port}"
    return RelyingParty(id=host, name="Tracecat", origin=origin)


def webauthn_unavailable_reason() -> str | None:
    try:
        get_relying_party()
    except WebAuthnUnavailableError as e:
        return str(e)
    return None


def build_server() -> Fido2Server:
    rp = get_relying_party()
    server = Fido2Server(
        PublicKeyCredentialRpEntity(name=rp.name, id=rp.id),
        attestation=AttestationConveyancePreference.NONE,
        verify_origin=lambda origin: origin == rp.origin,
    )
    server.timeout = CHALLENGE_TIMEOUT_MS  # pyright: ignore[reportAttributeAccessIssue]
    server.allowed_algorithms = [
        alg for alg in server.allowed_algorithms if alg.alg in ALLOWED_COSE_ALGORITHMS
    ]
    return server


def _challenge_key(purpose: ChallengePurpose, session_id: uuid.UUID) -> str:
    return f"mfa:webauthn:{purpose}:{session_id}"


async def store_challenge_state(
    purpose: ChallengePurpose, session_id: uuid.UUID, state: dict[str, Any]
) -> None:
    """Persist ceremony state for one session; a new ceremony replaces the old."""
    client = await get_redis_client()
    await client.set(
        _challenge_key(purpose, session_id),
        orjson.dumps(state).decode(),
        expire_seconds=CHALLENGE_TTL_SECONDS,
    )


async def consume_challenge_state(
    purpose: ChallengePurpose, session_id: uuid.UUID
) -> dict[str, Any] | None:
    """Return and delete ceremony state. Each challenge is usable once."""
    client = await get_redis_client()
    key = _challenge_key(purpose, session_id)
    raw = await client.get(key)
    if raw is None:
        return None
    # Only the caller whose delete removed the key may use the challenge.
    if await client.delete(key) != 1:
        return None
    state = orjson.loads(raw)
    return state if isinstance(state, dict) else None
