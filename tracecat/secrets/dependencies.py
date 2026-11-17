"""Dependencies for secret routes."""

from typing import Annotated

from fastapi import Depends
from fastapi.exceptions import RequestValidationError

from tracecat.identifiers.secret import SecretUUID


def secret_id_path_dependency(secret_id: str) -> SecretUUID:
    """Convert any secret ID format (UUID, short, legacy) to SecretUUID."""
    try:
        return SecretUUID.new(secret_id)
    except ValueError as exc:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("path", "secret_id"),
                    "msg": (
                        "Invalid secret ID. Expected a UUID, a short ID "
                        "(secret_<base62>), or a legacy ID (secret-<hex>)."
                    ),
                    "input": secret_id,
                }
            ]
        ) from exc


AnySecretIDPath = Annotated[SecretUUID, Depends(secret_id_path_dependency)]
"""A secret ID that can be a UUID, short ID (secret_xxx), or legacy format (secret-<hex>)."""
