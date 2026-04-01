"""Ed25519 signing key derivation and JWT operations for the internal OIDC issuer."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tracecat.config import USER_AUTH_SECRET

_HKDF_SALT = b"tracecat-mcp-oidc-issuer-v1"
_HKDF_INFO = b"ed25519-signing-key"
_ALGORITHM = "EdDSA"


def _derive_seed(secret: str) -> bytes:
    """Derive a 32-byte Ed25519 seed from the given secret via HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(secret.encode("utf-8"))


@lru_cache(maxsize=1)
def get_signing_key() -> Ed25519PrivateKey:
    """Return the Ed25519 private key derived from ``USER_AUTH_SECRET``.

    The key is deterministic: the same secret always produces the same
    keypair, so all replicas share one signing identity without key
    distribution.
    """
    seed = _derive_seed(USER_AUTH_SECRET)
    return Ed25519PrivateKey.from_private_bytes(seed)


def _get_public_key() -> Ed25519PublicKey:
    """Return the Ed25519 public key corresponding to the signing key."""
    return get_signing_key().public_key()


def _base64url_encode(data: bytes) -> str:
    """Base64url-encode without padding (RFC 7515 / RFC 7517)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _compute_kid(public_key: Ed25519PublicKey) -> str:
    """Compute a JWK thumbprint (RFC 7638) as the key ID.

    For OKP/Ed25519, the thumbprint is SHA-256 of the canonical JSON
    ``{"crv":"Ed25519","kty":"OKP","x":"<base64url>"}`` (members sorted).
    """
    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    x_value = _base64url_encode(raw_public)
    canonical = f'{{"crv":"Ed25519","kty":"OKP","x":"{x_value}"}}'
    digest = hashlib.sha256(canonical.encode("ascii")).digest()
    return _base64url_encode(digest)


@lru_cache(maxsize=1)
def get_public_jwk() -> dict[str, str]:
    """Return the Ed25519 public key as a JWK dict.

    Suitable for inclusion in a JWKS ``keys`` array.
    """
    public_key = _get_public_key()
    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "use": "sig",
        "alg": _ALGORITHM,
        "kid": _compute_kid(public_key),
        "x": _base64url_encode(raw_public),
    }


def mint_jwt(claims: dict[str, Any]) -> str:
    """Sign a JWT with the internal issuer's Ed25519 key.

    Args:
        claims: The JWT payload claims.

    Returns:
        The compact JWS string.
    """
    private_key = get_signing_key()
    kid = get_public_jwk()["kid"]
    return jwt.encode(
        payload=claims,
        key=private_key,
        algorithm=_ALGORITHM,
        headers={"kid": kid},
    )


def verify_jwt(
    token: str,
    *,
    audience: str | None = None,
    issuer: str | None = None,
) -> dict[str, Any]:
    """Verify and decode a JWT signed by the internal issuer.

    Args:
        token: The compact JWS string.
        audience: Expected ``aud`` claim value. Skipped if ``None``.
        issuer: Expected ``iss`` claim value. Skipped if ``None``.

    Returns:
        The decoded payload claims.

    Raises:
        jwt.InvalidTokenError: On any verification failure.
    """
    public_key = _get_public_key()
    options: dict[str, bool] = {}
    kwargs: dict[str, Any] = {
        "algorithms": [_ALGORITHM],
        "options": options,
    }
    if audience is not None:
        kwargs["audience"] = audience
    else:
        options["verify_aud"] = False
    if issuer is not None:
        kwargs["issuer"] = issuer
    return jwt.decode(token, key=public_key, **kwargs)
