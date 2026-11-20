"""Provider-neutral resolution of externally backed workspace secrets.

Core owns the safety rules once for every provider: remote reads are
deduplicated per ``(store, reference)`` within one call and never cached
across calls; each store is fetched once with all of its references; values
are projected onto declared keys here; failures surface as sanitized error
codes with no vendor message text and no chained cause; and fetched values
are dropped before any error is raised.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import orjson

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.logger import logger
from tracecat.secrets.encryption import decrypt_value
from tracecat.secrets.enums import SecretMappingMode, SecretResolutionErrorCode
from tracecat.secrets.schemas import SecretKeyMapping
from tracecat.secrets.types import (
    CheckResult,
    ExternalSecretReference,
    Failure,
    FetchOutcome,
    StoreContext,
)
from tracecat_ee.secrets.errors import SecretResolutionError
from tracecat_ee.secrets.stores.backends import get_backend


@dataclass(frozen=True, slots=True)
class ResolutionFailure:
    """Which reference failed and why. Carries no remote values."""

    reference: ExternalSecretReference
    failure: Failure


def project_value(
    mapping: SecretKeyMapping, value: str
) -> dict[str, str] | SecretResolutionErrorCode:
    """Map one fetched value onto its declared output keys.

    ``whole_string`` keeps the value verbatim; ``json`` selects string fields
    of a top-level object. Returns an error code instead of raising so no
    payload-bearing exception is created.
    """
    if mapping.mode == SecretMappingMode.WHOLE_STRING:
        return {mapping.keys[0]: value}
    try:
        fields: Any = orjson.loads(value)
    except orjson.JSONDecodeError:
        return SecretResolutionErrorCode.MALFORMED_JSON
    if not isinstance(fields, dict):
        return SecretResolutionErrorCode.MALFORMED_JSON

    projected: dict[str, str] = {}
    for selector in mapping.fields:
        if selector.field not in fields:
            return SecretResolutionErrorCode.MISSING_FIELD
        field_value = fields[selector.field]
        if not isinstance(field_value, str):
            return SecretResolutionErrorCode.NON_STRING_FIELD
        projected[selector.key] = field_value
    return projected


def decrypt_access_tokens(
    references: Sequence[ExternalSecretReference],
) -> dict[uuid.UUID, str]:
    """Decrypt each enabled store's access token, keyed by store ID.

    Stores without a usable token get nothing: no token, a disabled store,
    or a token that does not decrypt (for example after a failed key
    rotation). A provider that needs a token then fails the store with
    ``credential_missing``.
    """
    key: str | None = None
    tokens: dict[uuid.UUID, str] = {}
    for reference in references:
        encrypted = reference.encrypted_access_token
        if (
            encrypted is None
            or not reference.store_enabled
            or reference.store_id in tokens
        ):
            continue
        key = key or get_db_encryption_key()
        try:
            tokens[reference.store_id] = decrypt_value(encrypted, key=key).decode(
                "utf-8"
            )
        except Exception as e:
            logger.warning(
                "Secret store access token could not be decrypted",
                store_id=str(reference.store_id),
                error_type=type(e).__name__,
            )
    return tokens


def _store_context(
    reference: ExternalSecretReference, access_token: str | None
) -> StoreContext:
    return StoreContext(
        store_id=reference.store_id,
        provider=reference.provider,
        enabled=reference.store_enabled,
        config=reference.store_config,
        access_token=access_token,
    )


async def _fetch_store(
    store: StoreContext, keys: Sequence[str]
) -> dict[str, FetchOutcome]:
    """Run one provider fetch, turning every way it can fail into outcomes."""
    if not store.enabled:
        return dict.fromkeys(keys, Failure(SecretResolutionErrorCode.STORE_DISABLED))
    backend = get_backend(store.provider)
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
        return dict.fromkeys(keys, Failure(SecretResolutionErrorCode.UNKNOWN))


async def fetch_and_project(
    references: Sequence[ExternalSecretReference],
    access_tokens: Mapping[uuid.UUID, str],
) -> tuple[dict[str, dict[str, str]], ResolutionFailure | None]:
    """Fetch and map every reference, returning ``(resolved, failure)``.

    Never raises for a remote failure. On failure the returned mapping is
    empty and every fetched value has been dropped, so the frame that raises
    the sanitized error holds no fetched or partially projected values.
    ``access_tokens`` maps a store ID to its decrypted access token.
    """
    unique: dict[tuple[uuid.UUID, str], ExternalSecretReference] = {}
    for reference in references:
        unique.setdefault(reference.fetch_key, reference)
    by_store: dict[uuid.UUID, list[ExternalSecretReference]] = {}
    for reference in unique.values():
        by_store.setdefault(reference.store_id, []).append(reference)

    logger.info(
        "Resolving externally backed secrets",
        references=len(references),
        remote_reads=len(unique),
        stores=len(by_store),
    )
    results = await asyncio.gather(
        *(
            _fetch_store(
                _store_context(refs[0], access_tokens.get(store_id)),
                [ref.key for ref in refs],
            )
            for store_id, refs in by_store.items()
        )
    )
    fetched: dict[tuple[uuid.UUID, str], FetchOutcome] = {}
    resolved: dict[str, dict[str, str]] = {}
    failure: ResolutionFailure | None = None
    try:
        for store_id, outcomes in zip(by_store, results, strict=True):
            for key, outcome in outcomes.items():
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
        for outcomes in results:
            outcomes.clear()
    if failure is not None:
        resolved.clear()
        return {}, failure
    return resolved, None


def resolution_error(failure: ResolutionFailure) -> SecretResolutionError:
    """Build the sanitized error a run sees for one failed reference."""
    reference = failure.reference
    return get_backend(reference.provider).resolution_error(
        failure.failure.code,
        alias=reference.alias,
        environment=reference.environment,
        vendor_code=failure.failure.vendor_code,
    )


async def resolve_external_references(
    references: Sequence[ExternalSecretReference],
) -> dict[str, dict[str, str]]:
    """Resolve externally backed aliases to ``{alias: {key: value}}``.

    Any failure fails the whole batch with a sanitized
    :class:`SecretResolutionError` that has no chained cause.
    """
    if not references:
        return {}
    resolved, failure = await fetch_and_project(
        references, decrypt_access_tokens(references)
    )
    if failure is not None:
        error = resolution_error(failure)
        logger.warning(
            f"{get_backend(failure.reference.provider).display_name}-backed secret"
            " resolution failed",
            alias=error.alias,
            environment=error.environment,
            error_code=error.code.value,
            **{error.vendor_code_field: error.vendor_code},
        )
        raise error
    return resolved


async def check_external_reference(reference: ExternalSecretReference) -> CheckResult:
    """Verify one reference resolves without returning its value.

    The fetched value is dropped immediately; only key names are returned.
    """
    tokens = decrypt_access_tokens([reference])
    outcomes = await _fetch_store(
        _store_context(reference, tokens.get(reference.store_id)), [reference.key]
    )
    try:
        outcome = outcomes.get(
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
        outcomes.clear()
    if isinstance(projected, SecretResolutionErrorCode):
        return CheckResult(ok=False, error_code=projected)
    return CheckResult(ok=True, resolved_keys=sorted(projected.keys()))
