"""Provider-neutral resolution of externally backed workspace secrets.

Core owns the safety rules once for every provider: remote reads are
deduplicated per ``(store, reference)`` within one call and never cached
across calls; each store is fetched once with all of its references; values
are projected onto declared keys here; failures surface as sanitized error
codes with no vendor message text and no chained cause; and fetched values
are dropped before any error is raised.

Providers are looked up through a ``lookup`` callable rather than imported,
so provider modules can reuse these helpers without an import cycle. Callers
use :mod:`tracecat_ee.secrets.runtime`, which binds the registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

import orjson

from tracecat.exceptions import TracecatCredentialsError
from tracecat.logger import logger
from tracecat.secrets.enums import (
    SecretMappingMode,
    SecretResolutionErrorCode,
    SecretStoreProvider,
)
from tracecat.secrets.schemas import SecretKeyMapping
from tracecat.secrets.types import (
    CheckResult,
    ExternalSecretReference,
    Failure,
    FetchOutcome,
    FetchResult,
    StoreContext,
)

if TYPE_CHECKING:
    from tracecat_ee.secrets.stores.backends import AnyBackend

type BackendLookup = Callable[[SecretStoreProvider], AnyBackend]


class SecretResolutionError(TracecatCredentialsError):
    """Sanitized failure while resolving an externally backed workspace secret."""

    def __init__(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        provider_label: str,
        vendor_code: str | None = None,
        vendor_code_field: str = "provider_error_code",
    ) -> None:
        self.code = code
        self.alias = alias
        self.environment = environment
        self.vendor_code = vendor_code
        self.vendor_code_field = vendor_code_field
        suffix = f" ({provider_label} error code {vendor_code})" if vendor_code else ""
        super().__init__(
            f"Failed to resolve {provider_label}-backed secret {alias!r} in "
            f"environment {environment!r}: {code.value}{suffix}",
            detail={
                "secret_name": alias,
                "environment": environment,
                "error_code": code.value,
                vendor_code_field: vendor_code,
            },
        )


@dataclass(frozen=True, slots=True)
class ResolutionFailure:
    """Which reference failed and why. Carries no remote values."""

    reference: ExternalSecretReference
    failure: Failure


def project_value(
    mapping: SecretKeyMapping, value: str | Mapping[str, object]
) -> dict[str, str] | SecretResolutionErrorCode:
    """Map one fetched value onto its declared output keys.

    A string goes through ``whole_string`` (verbatim) or ``json`` (top-level
    object, selected string fields). A field map, as key-value stores return,
    goes straight to field selection and cannot be used whole. Returns an
    error code instead of raising so no payload-bearing exception is created.
    """
    fields: Mapping[str, Any]
    if isinstance(value, str):
        if mapping.mode == SecretMappingMode.WHOLE_STRING:
            return {mapping.keys[0]: value}
        try:
            parsed: Any = orjson.loads(value)
        except orjson.JSONDecodeError:
            return SecretResolutionErrorCode.MALFORMED_JSON
        if not isinstance(parsed, dict):
            return SecretResolutionErrorCode.MALFORMED_JSON
        fields = parsed
    else:
        if mapping.mode == SecretMappingMode.WHOLE_STRING:
            return SecretResolutionErrorCode.UNSUPPORTED_VALUE
        fields = value

    projected: dict[str, str] = {}
    for selector in mapping.fields:
        if selector.field not in fields:
            return SecretResolutionErrorCode.MISSING_FIELD
        field_value = fields[selector.field]
        if not isinstance(field_value, str):
            return SecretResolutionErrorCode.NON_STRING_FIELD
        projected[selector.key] = field_value
    return projected


def store_context(
    reference: ExternalSecretReference, access_token: str | None = None
) -> StoreContext:
    """Describe a reference's store for a provider fetch."""
    return StoreContext(
        store_id=reference.store_id,
        provider=reference.provider,
        enabled=reference.store_enabled,
        config=reference.store_config,
        access_token=access_token,
    )


async def _fetch_store(
    backend: AnyBackend,
    store: StoreContext,
    keys: Sequence[str],
) -> FetchResult:
    """Run one provider fetch, turning every way it can fail into outcomes."""
    if not store.enabled:
        failure = Failure(SecretResolutionErrorCode.STORE_DISABLED)
        return FetchResult(outcomes=dict.fromkeys(keys, failure))
    if backend.requires_access_token and store.access_token is None:
        failure = Failure(SecretResolutionErrorCode.CREDENTIAL_MISSING)
        return FetchResult(outcomes=dict.fromkeys(keys, failure))
    try:
        return await backend.fetch(store, keys)
    except Exception as e:
        # Providers report failures as outcomes. Anything raised is a bug;
        # log its type only, since the message may carry vendor text.
        logger.warning(
            "External secret provider raised during fetch",
            provider=str(store.provider),
            error_type=type(e).__name__,
        )
        failure = Failure(SecretResolutionErrorCode.UNKNOWN, type(e).__name__)
        return FetchResult(outcomes=dict.fromkeys(keys, failure))


async def _release(results: Sequence[FetchResult]) -> None:
    """Drop fetched values and run every provider cleanup step."""
    for result in results:
        if isinstance(result.outcomes, dict):
            result.outcomes.clear()
        if result.release is None:
            continue
        try:
            await result.release()
        except Exception as e:
            logger.warning(
                "External secret provider cleanup failed",
                error_type=type(e).__name__,
            )


async def fetch_and_project(
    references: Sequence[ExternalSecretReference],
    lookup: BackendLookup,
    access_tokens: Mapping[UUID, str] | None = None,
) -> tuple[dict[str, dict[str, str]], ResolutionFailure | None]:
    """Fetch and map every reference, returning ``(resolved, failure)``.

    Never raises for a remote failure. On failure the returned mapping is
    empty and every fetched value has been dropped, so the frame that raises
    the sanitized error holds no fetched or partially projected values.
    ``access_tokens`` maps a store ID to its decrypted access token.
    """
    unique: dict[tuple[UUID, str], ExternalSecretReference] = {}
    for reference in references:
        unique.setdefault(reference.fetch_key, reference)
    by_store: dict[UUID, list[ExternalSecretReference]] = {}
    for reference in unique.values():
        by_store.setdefault(reference.store_id, []).append(reference)

    logger.info(
        "Resolving externally backed secrets",
        references=len(references),
        remote_reads=len(unique),
        stores=len(by_store),
    )
    tokens = access_tokens or {}
    results = await asyncio.gather(
        *(
            _fetch_store(
                lookup(refs[0].provider),
                store_context(refs[0], tokens.get(store_id)),
                [ref.key for ref in refs],
            )
            for store_id, refs in by_store.items()
        )
    )
    fetched: dict[tuple[UUID, str], FetchOutcome] = {}
    resolved: dict[str, dict[str, str]] = {}
    failure: ResolutionFailure | None = None
    try:
        for store_id, result in zip(by_store, results, strict=True):
            for key, outcome in result.outcomes.items():
                fetched[(store_id, key)] = outcome
        for reference in references:
            outcome = fetched.get(
                reference.fetch_key, Failure(SecretResolutionErrorCode.UNKNOWN)
            )
            if isinstance(outcome, Failure):
                failure = ResolutionFailure(reference, outcome)
                break
            projected = project_value(reference.mapping, outcome)
            if isinstance(projected, SecretResolutionErrorCode):
                failure = ResolutionFailure(reference, Failure(projected))
                break
            resolved.setdefault(reference.alias, {}).update(projected)
    finally:
        fetched.clear()
        await _release(results)
    if failure is not None:
        resolved.clear()
        return {}, failure
    return resolved, None


def resolution_error(
    failure: ResolutionFailure, lookup: BackendLookup
) -> SecretResolutionError:
    """Build the sanitized error a run sees for one failed reference."""
    reference = failure.reference
    return lookup(reference.provider).resolution_error(
        failure.failure.code,
        alias=reference.alias,
        environment=reference.environment,
        vendor_code=failure.failure.vendor_code,
    )


async def resolve_references(
    references: Sequence[ExternalSecretReference],
    lookup: BackendLookup,
    access_tokens: Mapping[UUID, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Resolve externally backed aliases to ``{alias: {key: value}}``.

    Any failure fails the whole batch with a sanitized
    :class:`SecretResolutionError` that has no chained cause.
    """
    if not references:
        return {}
    resolved, failure = await fetch_and_project(references, lookup, access_tokens)
    if failure is not None:
        error = resolution_error(failure, lookup)
        logger.warning(
            f"{lookup(failure.reference.provider).display_name}-backed secret"
            " resolution failed",
            alias=error.alias,
            environment=error.environment,
            error_code=error.code.value,
            **{error.vendor_code_field: error.vendor_code},
        )
        raise error
    return resolved


async def check_reference(
    reference: ExternalSecretReference,
    lookup: BackendLookup,
    access_token: str | None = None,
) -> CheckResult:
    """Verify one reference resolves without returning its value.

    The fetched value is dropped immediately; only key names are returned.
    """
    result = await _fetch_store(
        lookup(reference.provider),
        store_context(reference, access_token),
        [reference.key],
    )
    try:
        outcome = result.outcomes.get(
            reference.key, Failure(SecretResolutionErrorCode.UNKNOWN)
        )
        if isinstance(outcome, Failure):
            return CheckResult(
                ok=False,
                error_code=outcome.code,
                provider_error_code=outcome.vendor_code,
            )
        projected = project_value(reference.mapping, outcome)
    finally:
        await _release([result])
    if isinstance(projected, SecretResolutionErrorCode):
        return CheckResult(ok=False, error_code=projected)
    return CheckResult(ok=True, resolved_keys=sorted(projected.keys()))
