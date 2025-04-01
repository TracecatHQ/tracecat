"""Hashing utilities for Tracecat."""

from __future__ import annotations

import hashlib
from typing import Literal

HashAlgorithm = Literal["sha256"]


def digest(data: bytes) -> str:
    """Compute the SHA-256 digest of the data.

    Args:
        data: The data to hash
        algorithm: The hashing algorithm to use (currently only SHA-256 supported)

    Returns:
        str: The formatted digest string (e.g. "sha256:...")

    Raises:
        ValueError: If an unsupported algorithm is specified
    """
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def validate_digest(digest: str, data: bytes) -> None:
    """Validate the digest of the data.

    Args:
        digest: The digest string to validate against (format: "algorithm:hash")
        data: The data to verify

    Raises:
        ValueError: If the digest format is invalid or the checksum doesn't match
    """
    # Verify checksum
    digest_parts = digest.split(":")
    if len(digest_parts) != 2 or digest_parts[0] != "sha256":
        raise ValueError(f"Invalid digest format: {digest}")

    expected_digest = digest_parts[1]
    actual_digest = hashlib.sha256(data).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError(
            f"Checksum mismatch: expected {expected_digest}, got {actual_digest}"
        )
