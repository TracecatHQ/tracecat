"""Unit tests for tracecat.mcp.oidc.signing — ECDSA P-256 key derivation and JWT ops."""

from __future__ import annotations

import base64
import hashlib
import time

import pytest
from jwt import InvalidTokenError

from tracecat.mcp.oidc import signing


@pytest.fixture(autouse=True)
def _clear_signing_caches(monkeypatch: pytest.MonkeyPatch):  # pyright: ignore[reportUnusedFunction]
    """Clear lru_cache on signing functions and set a stable test secret."""
    monkeypatch.setattr(signing, "USER_AUTH_SECRET", "test-signing-secret")
    signing.get_signing_key.cache_clear()
    signing.get_public_jwk.cache_clear()
    yield
    signing.get_signing_key.cache_clear()
    signing.get_public_jwk.cache_clear()


def test_derive_seed_is_deterministic() -> None:
    """Same secret always produces the same private scalar."""
    seed_a = signing._derive_seed("stable-secret")
    seed_b = signing._derive_seed("stable-secret")
    assert seed_a == seed_b
    assert isinstance(seed_a, int)
    assert seed_a > 0


def test_derive_seed_differs_for_different_secrets() -> None:
    """Different secrets produce different seeds."""
    seed_a = signing._derive_seed("secret-one")
    seed_b = signing._derive_seed("secret-two")
    assert seed_a != seed_b


def test_get_signing_key_is_deterministic() -> None:
    """Same USER_AUTH_SECRET always produces the same key pair."""
    key_a = get_signing_key_public_bytes()
    signing.get_signing_key.cache_clear()
    key_b = get_signing_key_public_bytes()
    assert key_a == key_b


def get_signing_key_public_bytes() -> bytes:
    """Helper to get public key bytes for comparison."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return (
        signing.get_signing_key()
        .public_key()
        .public_bytes(encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo)
    )


def test_mint_verify_roundtrip() -> None:
    """mint_jwt → verify_jwt preserves all claims."""
    claims = {
        "sub": "user-123",
        "iss": "https://issuer.example.com",
        "aud": "test-audience",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "custom": "value",
    }
    token = signing.mint_jwt(claims)

    decoded = signing.verify_jwt(
        token, audience="test-audience", issuer="https://issuer.example.com"
    )

    assert decoded["sub"] == "user-123"
    assert decoded["custom"] == "value"
    assert decoded["iss"] == "https://issuer.example.com"
    assert decoded["aud"] == "test-audience"


def test_verify_jwt_rejects_tampered_payload() -> None:
    """Tampering with the payload invalidates the signature."""
    claims = {
        "sub": "user-123",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = signing.mint_jwt(claims)
    header, _payload, sig = token.split(".")
    # Replace payload with a different one.
    tampered_payload = base64.urlsafe_b64encode(b'{"sub":"hacker"}').rstrip(b"=")
    tampered_token = f"{header}.{tampered_payload.decode()}.{sig}"

    with pytest.raises(InvalidTokenError):
        signing.verify_jwt(tampered_token)


def test_verify_jwt_validates_audience() -> None:
    """Audience mismatch causes verification failure."""
    claims = {
        "sub": "user-123",
        "aud": "expected-audience",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = signing.mint_jwt(claims)

    with pytest.raises(InvalidTokenError):
        signing.verify_jwt(token, audience="wrong-audience")


def test_verify_jwt_validates_issuer() -> None:
    """Issuer mismatch causes verification failure."""
    claims = {
        "sub": "user-123",
        "iss": "https://expected-issuer.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = signing.mint_jwt(claims)

    with pytest.raises(InvalidTokenError):
        signing.verify_jwt(token, issuer="https://wrong-issuer.com")


def test_verify_jwt_skips_aud_when_none() -> None:
    """When audience=None, aud claim is not verified."""
    claims = {
        "sub": "user-123",
        "aud": "some-audience",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = signing.mint_jwt(claims)

    decoded = signing.verify_jwt(token, audience=None)
    assert decoded["aud"] == "some-audience"


def test_get_public_jwk_format() -> None:
    """Public JWK has all required EC P-256 fields."""
    jwk = signing.get_public_jwk()

    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"
    assert jwk["alg"] == "ES256"
    assert jwk["use"] == "sig"
    assert "kid" in jwk
    assert "x" in jwk
    assert "y" in jwk


def test_compute_kid_is_rfc7638_thumbprint() -> None:
    """Key ID matches RFC 7638 JWK thumbprint computation."""
    public_key = signing._get_public_key()
    kid = signing._compute_kid(public_key)

    # Manually compute the thumbprint.
    numbers = public_key.public_numbers()
    x_value = signing._base64url_encode(numbers.x.to_bytes(32, "big"))
    y_value = signing._base64url_encode(numbers.y.to_bytes(32, "big"))
    canonical = f'{{"crv":"P-256","kty":"EC","x":"{x_value}","y":"{y_value}"}}'
    expected_digest = hashlib.sha256(canonical.encode("ascii")).digest()
    expected_kid = signing._base64url_encode(expected_digest)

    assert kid == expected_kid
