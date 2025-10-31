from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    TypeAdapter,
    computed_field,
    model_validator,
)

"""
IMPORTANT: Pydantic Annotated Metadata Ordering

Pydantic v2 processes Annotated metadata in a specific order that is CRITICAL to understand:

1. RIGHT-TO-LEFT pass: Pydantic walks metadata from right to left, executing BeforeValidators
   immediately as it encounters them.

2. LEFT-TO-RIGHT pass: After reaching the leftmost item, Pydantic walks back left to right,
   executing AfterValidators, applying Field constraints, etc.

This means:
- BeforeValidators on the RIGHT execute BEFORE those on the LEFT
- Field(discriminator="type") must see the final, transformed data
- If your BeforeValidator adds/modifies fields that discriminator needs,
  the BeforeValidator MUST be to the RIGHT of Field()

Example of CORRECT ordering:
```python
type T = Annotated[
    Union[A, B],
    Field(discriminator="type"),                    # ← Needs "type" field to exist
    BeforeValidator(add_type_field_if_missing),     # ← Adds "type" field (runs first)
]
```

Example of INCORRECT ordering:
```python
type T = Annotated[
    Union[A, B],
    BeforeValidator(add_type_field_if_missing),     # ← Runs second (too late!)
    Field(discriminator="type"),                    # ← Runs first, "type" doesn't exist yet
]
```

This is why our RegistrySecretType uses Field() first, then BeforeValidator() - the validator
adds the "type" field that the discriminator requires.

See: https://docs.pydantic.dev/latest/concepts/validators/#ordering-of-validators-within-annotated
"""

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""


class RegistrySecret(BaseModel):
    type: Literal["custom"] = Field(default="custom", frozen=True)

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
            # Cannot have a RegistrySecret with _oauth suffix
            raise ValueError(
                "OAuth secrets are not allowed to have keys or optional_keys"
            )

        if v.keys is None and v.optional_keys is None:
            raise ValueError(
                "At least one of 'keys' or 'optional_keys' must be specified"
            )
        return v

    def __hash__(self) -> int:
        """Custom hash implementation based on relevant fields."""
        return hash(
            (
                self.type,
                self.name,
                tuple(self.keys) if self.keys else None,
                tuple(self.optional_keys) if self.optional_keys else None,
                self.optional,
            )
        )


class RegistryOAuthSecret(BaseModel):
    """OAuth secret for a provider."""

    type: Literal["oauth"] = Field(default="oauth", frozen=True)
    provider_id: str
    """The provider id of the secret."""
    grant_type: Literal["authorization_code", "client_credentials"]
    """The grant type for the OAuth secret."""
    optional: bool = False
    """Indicates if the OAuth secret is optional."""

    @computed_field
    @property
    def name(self) -> str:
        return f"{self.provider_id}_oauth"

    @property
    def token_name(self) -> str:
        """The keyname for the OAuth secret.

        `SECRETS.<provider_id>.<prefix>_[SERVICE|USER]_TOKEN`

        <prefix> is the provider_id in uppercase.
        """
        prefix = self.provider_id.upper()
        match self.grant_type:
            case "client_credentials":
                return f"{prefix}_SERVICE_TOKEN"
            case "authorization_code":
                return f"{prefix}_USER_TOKEN"
            case _:
                raise ValueError(f"Invalid grant type: {self.grant_type}")

    def __hash__(self) -> int:
        """Custom hash implementation based on relevant fields."""
        return hash((self.type, self.provider_id, self.grant_type, self.optional))


# Custom validator to handle backward compatibility
def _validate_registry_secret_type(value: Any) -> Any:
    """Add backward compatibility for legacy RegistrySecret objects without 'type' field.

    This validator ensures that:
    1. Legacy custom secrets (with keys/optional_keys) get type="custom"
    2. Legacy OAuth secrets (names ending in '_oauth') get type="oauth"
    3. Other legacy secrets without clear type indicators will fail validation
    4. Modern secrets with explicit 'type' field are passed through unchanged
    """
    if isinstance(value, dict) and "type" not in value:
        if "keys" in value or "optional_keys" in value:
            # Legacy custom secret with keys - set type to "custom"
            value = {**value, "type": "custom"}
        elif value.get("name", "").endswith("_oauth"):
            # Legacy OAuth secret - extract provider_id and set defaults
            provider_id = value["name"].replace("_oauth", "")
            value = {
                **value,
                "type": "oauth",
                "provider_id": provider_id,
                "grant_type": "authorization_code",  # Default grant type for legacy
            }
        # Note: We deliberately don't add a fallback 'else' clause here.
        # Legacy secrets that don't fit either pattern should fail validation
        # to maintain data integrity. This forces proper migration.
    return value


# Tagged union with backward compatibility
#
# CRITICAL ORDERING: Field() must come BEFORE BeforeValidator() because:
# 1. Pydantic processes Annotated metadata RIGHT-TO-LEFT for BeforeValidators
# 2. BeforeValidator (rightmost) runs FIRST, adding the "type" field to legacy data
# 3. Field(discriminator="type") runs SECOND, using the now-present "type" field
# 4. If reversed, Field() would run first and fail because "type" doesn't exist yet
type RegistrySecretType = Annotated[
    RegistrySecret | RegistryOAuthSecret,
    Field(discriminator="type"),  # ← Runs second: needs "type" field
    BeforeValidator(_validate_registry_secret_type),  # ← Runs first: adds "type" field
]
RegistrySecretTypeValidator: TypeAdapter[RegistrySecretType] = TypeAdapter(
    RegistrySecretType
)
