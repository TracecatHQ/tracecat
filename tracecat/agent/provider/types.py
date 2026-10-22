"""Domain types for LLM provider management."""

from dataclasses import dataclass


@dataclass(kw_only=True, slots=True)
class ResolvedCustomProviderCredentials:
    """Decrypted credentials for a custom LLM provider."""

    api_key: str | None = None
    custom_headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ProviderKeyCheck:
    """How to check a first-class provider API key with ``GET /models``."""

    api_key_field: str
    base_url_field: str | None
    models_base_url: str
    api_key_header: str | None = None
    extra_headers: tuple[tuple[str, str], ...] = ()
    invalid_key_status_codes: frozenset[int] = frozenset({401})
    # When set, only these error codes reject; restricted keys may lack model read.
    rejected_error_codes: frozenset[str] | None = None
