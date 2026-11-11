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
    # Base URL hosts the provider runs, e.g. regional endpoints; "*." matches subdomains.
    official_hosts: frozenset[str] = frozenset()
    invalid_key_status_codes: frozenset[int] = frozenset({401})
    # Required for an invalid_key_status_codes rejection; 403 still denies.
    invalid_key_error_codes: frozenset[str] | None = None
    # Required for any rejection, 403 included; restricted keys may lack model read.
    rejected_error_codes: frozenset[str] | None = None
