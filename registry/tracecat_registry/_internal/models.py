from typing import Annotated, Self

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
        if isinstance(v.name, str) and v.name.endswith("_oauth"):
            if v.keys is not None or v.optional_keys is not None:
                raise ValueError("OAuth secrets cannot have keys or optional_keys")
        else:
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

    @property
    def is_oauth(self) -> bool:
        return self.name.endswith("_oauth")

    @property
    def provider_id(self) -> str:
        return self.name.replace("_oauth", "")

    @classmethod
    def oauth(cls, provider_id: str) -> Self:
        """Create an OAuth secret for the specified provider.

        Args:
            provider_id: The identifier for the OAuth provider (e.g., 'microsoft_graph').

        Returns:
            A new RegistrySecret instance configured for OAuth authentication.
        """
        return cls(name=f"{provider_id}_oauth")
