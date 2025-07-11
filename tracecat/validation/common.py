from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.alias_generators import to_camel

from tracecat.expressions.common import ExprType
from tracecat.expressions.validation import TemplateValidator
from tracecat.logger import logger
from tracecat.secrets.models import SecretSearch
from tracecat.secrets.service import SecretsService
from tracecat.validation.models import ExprValidationResult


def json_schema_to_pydantic(
    schema: dict[str, Any],
    base_schema: dict[str, Any] | None = None,
    *,
    name: str = "DynamicModel",
    root_config: ConfigDict | None = None,
) -> type[BaseModel]:
    """Recursively convert a JSON schema to a Pydantic model.

    This function will recursively convert a JSON schema to a Pydantic model.
    It will also handle references to other schemas in the schema.
    """

    if base_schema is None:
        base_schema = schema

    def resolve_ref(ref: str) -> dict[str, Any]:
        parts = ref.split("/")
        current = base_schema
        for part in parts[1:]:  # Skip the first '#' part
            current = current[part]
        return current

    def create_field(prop_schema: dict[str, Any], enum_field_name: str) -> type:
        if "$ref" in prop_schema:
            referenced_schema = resolve_ref(prop_schema["$ref"])
            # Pass the original model name for context if the ref is to a simple type that becomes an enum
            return json_schema_to_pydantic(referenced_schema, base_schema, name=name)

        type_ = prop_schema.get("type")
        if "enum" in prop_schema:
            enum_values = prop_schema["enum"]
            if not enum_values:
                raise ValueError(
                    f"JSON schema field '{enum_field_name}' defines an empty enum list, which is not allowed."
                )
            # Ensure all enum values are of the same basic type (str, int, etc.) or handle mixed types if allowed.
            # Pydantic's Literal usually expects uniform literal types.
            # Example: Literal[1, "apple"] is valid, but might not be what JSON schema implies without more context.
            return Literal[*enum_values]  # type: ignore[valid-type]

        if type_ == "object":
            # Pass the potential title or name of the object schema as the model name
            object_model_name = prop_schema.get(
                "title", prop_schema.get("name", f"{to_camel(enum_field_name)}Model")
            )
            return json_schema_to_pydantic(
                prop_schema, base_schema, name=object_model_name
            )
        elif type_ == "array":
            items_schema = prop_schema.get("items", {})
            # Pass the field_name_for_enum for context in case array items are enums/objects
            return list[create_field(items_schema, f"{enum_field_name}Item")]
        elif type_ == "string":
            format_type = prop_schema.get("format")
            if format_type == "date-time":
                return datetime
            return str
        elif type_ == "integer":
            return int
        elif type_ == "number":
            return float
        elif type_ == "boolean":
            return bool
        else:
            return Any  # type: ignore

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    fields = {}
    for prop_name, prop_schema_val in properties.items():
        # Pass prop_name to create_field for enum name generation context
        created_type = create_field(prop_schema_val, prop_name)
        field_type = Annotated[created_type, TemplateValidator()]
        field_params = {}

        if "description" in prop_schema_val:
            field_params["description"] = prop_schema_val["description"]

        if prop_name not in required:
            field_type = field_type | None
            field_params["default"] = None

        fields[prop_name] = (field_type, Field(**field_params))

    model_name = schema.get("title", name)
    return create_model(model_name, **fields, __config__=root_config)


async def secret_validator(
    *, name: str, key: str, loc: str, environment: str
) -> ExprValidationResult:
    # (1) Check if the secret is defined
    async with SecretsService.with_session() as service:
        defined_secret = await service.search_secrets(
            SecretSearch(names={name}, environment=environment)
        )
        logger.info("Secret search results", defined_secret=defined_secret)
        if (n_found := len(defined_secret)) != 1:
            logger.debug(
                "Secret not found in SECRET context usage",
                n_found=n_found,
                secret_name=name,
                environment=environment,
            )
            return ExprValidationResult(
                status="error",
                msg=f"[{loc}]\n\nFound {n_found} secrets matching {name!r} in the {environment!r} environment.",
                expression_type=ExprType.SECRET,
            )

        # There should only be 1 secret
        decrypted_keys = service.decrypt_keys(defined_secret[0].encrypted_keys)
        defined_keys = {kv.key for kv in decrypted_keys}

    # (2) Check if the secret has the correct keys
    if key not in defined_keys:
        logger.error(
            "Missing secret keys in SECRET context usage",
            secret_name=name,
            missing_key=key,
        )
        return ExprValidationResult(
            status="error",
            msg=f"Secret {name!r} is missing key: {key!r}",
            expression_type=ExprType.SECRET,
        )
    return ExprValidationResult(status="success", expression_type=ExprType.SECRET)


def get_validators():
    return {ExprType.SECRET: secret_validator}
