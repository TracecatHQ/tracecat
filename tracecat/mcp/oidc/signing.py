"""ECDSA (P-256) signing key derivation and JWT operations for the internal OIDC issuer.

Uses ES256 (ECDSA with P-256 / SHA-256) for compatibility with FastMCP's
JWTVerifier which does not support EdDSA.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
    derive_private_key,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tracecat.config import USER_AUTH_SECRET

_HKDF_SALT = b"tracecat-mcp-oidc-issuer-v1"
_HKDF_INFO = b"ec-p256-signing-key"
_ALGORITHM = "ES256"


def _derive_seed(secret: str) -> int:
    """Derive a P-256 private scalar from the given secret via HKDF-SHA256.

    Returns an integer in [1, n-1] where n is the P-256 curve order.
    """
    # P-256 curve order
    n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(secret.encode("utf-8"))
    # Reduce to [1, n-1]
    return (int.from_bytes(raw, "big") % (n - 1)) + 1


@lru_cache(maxsize=1)
def get_signing_key() -> EllipticCurvePrivateKey:
    """Return the ECDSA P-256 private key derived from ``USER_AUTH_SECRET``.

    The key is deterministic: the same secret always produces the same
    keypair, so all replicas share one signing identity without key
    distribution.
    """
    private_value = _derive_seed(USER_AUTH_SECRET)
    return derive_private_key(private_value, SECP256R1())


def _get_public_key() -> EllipticCurvePublicKey:
    """Return the ECDSA P-256 public key corresponding to the signing key."""
    return get_signing_key().public_key()


def _base64url_encode(data: bytes) -> str:
    """Base64url-encode without padding (RFC 7515 / RFC 7517)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _compute_kid(public_key: EllipticCurvePublicKey) -> str:
    """Compute a JWK thumbprint (RFC 7638) as the key ID.

    For EC/P-256, the thumbprint is SHA-256 of the canonical JSON
    ``{"crv":"P-256","kty":"EC","x":"<base64url>","y":"<base64url>"}``
    (members sorted alphabetically).
    """
    numbers = public_key.public_numbers()
    x_bytes = numbers.x.to_bytes(32, "big")
    y_bytes = numbers.y.to_bytes(32, "big")
    x_value = _base64url_encode(x_bytes)
    y_value = _base64url_encode(y_bytes)
    canonical = f'{{"crv":"P-256","kty":"EC","x":"{x_value}","y":"{y_value}"}}'
    digest = hashlib.sha256(canonical.encode("ascii")).digest()
    return _base64url_encode(digest)


@lru_cache(maxsize=1)
def get_public_jwk() -> dict[str, str]:
    """Return the ECDSA P-256 public key as a JWK dict.

    Suitable for inclusion in a JWKS ``keys`` array.
    """
    public_key = _get_public_key()
    numbers = public_key.public_numbers()
    x_bytes = numbers.x.to_bytes(32, "big")
    y_bytes = numbers.y.to_bytes(32, "big")
    return {
        "kty": "EC",
        "crv": "P-256",
        "use": "sig",
        "alg": _ALGORITHM,
        "kid": _compute_kid(public_key),
        "x": _base64url_encode(x_bytes),
        "y": _base64url_encode(y_bytes),
    }


def mint_jwt(claims: dict[str, Any]) -> str:
    """Sign a JWT with the internal issuer's ECDSA P-256 key.

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
