"""Secret identifiers."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import StringConstraints

from tracecat.identifiers.common import TracecatUUID

# Prefixes
SECRET_ID_PREFIX = "secret_"
LEGACY_SECRET_ID_PREFIX = "secret-"

# Patterns for validation
_SECRET_ID_SHORT_PATTERN = rf"{SECRET_ID_PREFIX}[0-9a-zA-Z]+"
_LEGACY_SECRET_ID_PATTERN = r"secret-[0-9a-f]{32}"

# Short ID type (used as TracecatUUID type parameter)
SecretIDShort = Annotated[str, StringConstraints(pattern=_SECRET_ID_SHORT_PATTERN)]
LegacySecretID = Annotated[str, StringConstraints(pattern=_LEGACY_SECRET_ID_PATTERN)]


class SecretUUID(TracecatUUID[SecretIDShort]):
    """UUID for secret resources.

    Supports:
    - Native UUID format (database storage)
    - Short ID format: `secret_xxx`
    - Legacy format: `secret-<32hex>`
    """

    prefix = SECRET_ID_PREFIX
    legacy_prefix = LEGACY_SECRET_ID_PREFIX


AnySecretID = SecretUUID | SecretIDShort | LegacySecretID | uuid.UUID

# Keep SecretID as alias for backward compatibility in type hints
SecretID = uuid.UUID
"""A unique ID for a secret. Now uses native UUID format."""
