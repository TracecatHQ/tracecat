from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, create_model

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
) -> type[BaseModel]:
    if base_schema is None:
        base_schema = schema

    def resolve_ref(ref: str) -> dict[str, Any]:
        parts = ref.split("/")
        current = base_schema
        for part in parts[1:]:  # Skip the first '#' part
            current = current[part]
        return current

    def create_field(prop_schema: dict[str, Any]) -> type:
        if "$ref" in prop_schema:
            referenced_schema = resolve_ref(prop_schema["$ref"])
            return json_schema_to_pydantic(referenced_schema, base_schema)

        type_ = prop_schema.get("type")
        if type_ == "object":
            return json_schema_to_pydantic(prop_schema, base_schema)
        elif type_ == "array":
            items = prop_schema.get("items", {})
            return list[create_field(items)]
        elif type_ == "string":
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
    for prop_name, prop_schema in properties.items():
        field_type = Annotated[create_field(prop_schema), TemplateValidator()]
        field_params = {}

        if "description" in prop_schema:
            field_params["description"] = prop_schema["description"]

        if prop_name not in required:
            field_type = Optional[field_type]  # noqa: UP007
            field_params["default"] = None

        fields[prop_name] = (field_type, Field(**field_params))

    model_name = schema.get("title", name)
    return create_model(model_name, **fields)


async def secret_validator(
    *, name: str, key: str, loc: str, environment: str
) -> ExprValidationResult:
    # (1) Check if the secret is defined
    async with SecretsService.with_session() as service:
        defined_secret = await service.search_secrets(
            SecretSearch(names=[name], environment=environment)  # type: ignore
        )
        logger.info("Secret search results", defined_secret=defined_secret)
        if (n_found := len(defined_secret)) != 1:
            logger.error(
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
