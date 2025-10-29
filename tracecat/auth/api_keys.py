from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

from tracecat.auth.crypto import generate_token

DEFAULT_API_KEY_PREFIX = "sk_"
DEFAULT_SUFFIX_LENGTH = 6
SALT_BYTES = 16
DIGEST_SIZE = 32


@dataclass(slots=True, frozen=True)
class GeneratedApiKey:
    """Represents a freshly generated API key and its persistence metadata."""

    raw: str
    hashed: str
    salt_b64: str
    suffix: str


def _hash_api_key(value: str, salt: bytes) -> str:
    # Use blake2b for faster hashing with built-in keyed mode (salt parameter).
    # blake2b is cryptographically secure and faster than sha256 for this use case.
    digest = hashlib.blake2b(
        value.encode("utf-8"),
        salt=salt,
        digest_size=DIGEST_SIZE,
    )
    return digest.hexdigest()


def generate_api_key(
    *,
    prefix: str = DEFAULT_API_KEY_PREFIX,
    length: int = 32,
    suffix_length: int = DEFAULT_SUFFIX_LENGTH,
) -> GeneratedApiKey:
    """
    Generate and hash a secure API key.

    Args:
        prefix: Prefix to prepend to the key. Defaults to ``"sk_"``.
        length: Number of random bytes used to generate the key (base62 encoded).
        suffix_length: Number of trailing characters to surface for display.

    Returns:
        `GeneratedApiKey` containing the raw key (return this once),
        the hashed value, salt (base64), and a display suffix.
    """
    if suffix_length <= 0:
        raise ValueError("suffix_length must be positive")
    raw = generate_token(prefix=prefix, length=length)
    salt = secrets.token_bytes(SALT_BYTES)
    hashed = _hash_api_key(raw, salt)
    suffix = raw[-suffix_length:]
    return GeneratedApiKey(
        raw=raw,
        hashed=hashed,
        salt_b64=base64.b64encode(salt).decode("ascii"),
        suffix=suffix,
    )


def verify_api_key(candidate: str, salt_b64: str, expected_hash: str) -> bool:
    """
    Verify a candidate API key against the stored hash and salt.
    """
    if not salt_b64 or not expected_hash or not candidate:
        return False
    try:
        salt = base64.b64decode(salt_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False
    computed = _hash_api_key(candidate, salt)
    return secrets.compare_digest(computed, expected_hash)
