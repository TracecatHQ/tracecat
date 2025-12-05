"""Dependencies for secret routes."""

from typing import Annotated

from fastapi import Depends

from tracecat.identifiers.secret import SecretUUID


def secret_id_path_dependency(secret_id: str) -> SecretUUID:
    """Convert any secret ID format (UUID, short, legacy) to SecretUUID."""
    return SecretUUID.new(secret_id)


AnySecretIDPath = Annotated[SecretUUID, Depends(secret_id_path_dependency)]
"""A secret ID that can be a UUID, short ID (secret_xxx), or legacy format (secret-<hex>)."""
