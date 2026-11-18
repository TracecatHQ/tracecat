"""Entry points that resolve externally backed secrets through the registry."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.logger import logger
from tracecat.secrets.encryption import decrypt_value
from tracecat.secrets.types import CheckResult, ExternalSecretReference
from tracecat_ee.secrets.resolver import check_reference, resolve_references
from tracecat_ee.secrets.stores.backends import get_backend


def decrypt_access_tokens(
    references: Sequence[ExternalSecretReference],
) -> dict[uuid.UUID, str]:
    """Decrypt each enabled store's access token, keyed by store ID.

    Stores without a usable token get nothing: no token, a disabled store,
    or a token that does not decrypt (for example after a failed key
    rotation). Core then fails the store with ``store_disabled`` or
    ``credential_missing`` before calling its provider.
    """
    key = get_db_encryption_key()
    tokens: dict[uuid.UUID, str] = {}
    for reference in references:
        encrypted = reference.encrypted_access_token
        if (
            encrypted is None
            or not reference.store_enabled
            or reference.store_id in tokens
        ):
            continue
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


async def resolve_external_references(
    references: Sequence[ExternalSecretReference],
) -> dict[str, dict[str, str]]:
    """Resolve references from any provider to ``{alias: {key: value}}``."""
    if not references:
        return {}
    return await resolve_references(
        references, get_backend, decrypt_access_tokens(references)
    )


async def check_external_reference(
    reference: ExternalSecretReference,
) -> CheckResult:
    """Verify one reference resolves without returning its value."""
    tokens = decrypt_access_tokens([reference])
    return await check_reference(reference, get_backend, tokens.get(reference.store_id))
