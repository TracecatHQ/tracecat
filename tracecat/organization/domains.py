"""Utilities for organization domain normalization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedDomain:
    """Normalized domain values for persistence and lookup."""

    domain: str
    normalized_domain: str


def normalize_domain(domain: str) -> NormalizedDomain:
    """Normalize a domain for display and stable lookup.

    Rules:
    - Trim surrounding whitespace
    - Drop any trailing dot
    - Lowercase
    - Normalize to punycode/ASCII for global unique matching
    """
    cleaned = domain.strip().rstrip(".").lower()
    if not cleaned:
        raise ValueError("Domain cannot be empty")

    try:
        normalized = cleaned.encode("idna").decode("ascii").lower()
    except UnicodeError as e:
        raise ValueError("Invalid domain") from e

    if not normalized:
        raise ValueError("Domain cannot be empty")

    return NormalizedDomain(domain=cleaned, normalized_domain=normalized)
