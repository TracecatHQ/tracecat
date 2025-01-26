from typing import Annotated

from pydantic import BaseModel, StringConstraints, model_validator

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""


class RegistrySecret(BaseModel):
    name: SecretName
    """The name of the secret."""

    keys: list[SecretKey] | None = None
    """Keys that are required to be set in the environment."""

    optional_keys: list[SecretKey] | None = None
    """Keys that are optional to be set in the environment."""

    optional: bool = False
    """Indicates if the secret is optional."""

    @model_validator(mode="after")
    def validate_keys(cls, v):
        if v.keys is None and v.optional_keys is None:
            raise ValueError(
                "At least one of 'keys' or 'optional_keys' must be specified"
            )
        return v

    def __hash__(self) -> int:
        """Custom hash implementation based on relevant fields."""
        return hash(
            (
                self.name,
                tuple(self.keys) if self.keys else None,
                tuple(self.optional_keys) if self.optional_keys else None,
                self.optional,
            )
        )
