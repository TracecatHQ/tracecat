from typing import Annotated, Any, TypeVar

from pydantic import GetCoreSchemaHandler, ValidationInfo, ValidatorFunctionWrapHandler
from pydantic.functional_validators import WrapValidator
from pydantic_core import core_schema

from tracecat.expressions.patterns import STANDALONE_TEMPLATE


def is_standalone_template(template: str) -> bool:
    """Check if a string is a complete template expression (${{...}})"""
    return STANDALONE_TEMPLATE.match(template) is not None


T = TypeVar("T")


# We can bundle validators and unpack them in a single expression
class TemplateValidator:
    def __new__(cls):
        return WrapValidator(cls.maybe_templated_expression)

    @classmethod
    def maybe_templated_expression(
        cls, v: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Any:
        try:
            # Quick win for simple expressions
            return handler(v)
        except Exception:
            # Fallback to recursive validation for template expressions
            return template_or_original_validator(v, handler)


def template_or_original_validator(
    v: Any, handler: ValidatorFunctionWrapHandler
) -> Any:
    """Allows for templated expressions in the input data.

    This validator is used to validate the input data for templated expressions.
    It will skip validation for template expressions and only validate the input data for the expected type.
    It allows expressions to exist at any level of the input data, including the top level.

    e.g.
    ```python
    class Test(BaseModel):
        a: Annotated[dict[str, list[int]], TemplateValidator()]

    print(Test(a={"b": "${{ my_list }}"}).model_dump())
    ```
    """
    # First check if it's a template string
    if isinstance(v, str) and is_standalone_template(v):
        return v
    # If not a template, validate against the original schema
    return handler(v)


class CoreSchemaTemplateValidator:
    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        schema = handler(source_type)
        return self._process_nested_schema(schema)

    def _process_nested_schema(
        self, schema: core_schema.CoreSchema
    ) -> core_schema.CoreSchema:
        """Process nested schema types recursively.

        Args:
            schema: The schema to process

        Returns:
            Processed schema with template validation added to nested types
        """
        match schema:
            case {"type": "dict", "values_schema": values_schema}:
                schema["values_schema"] = self._process_nested_schema(values_schema)
            case {"type": "tuple", "items_schema": items_schema}:
                schema["items_schema"] = [
                    self._process_nested_schema(s) for s in items_schema
                ]
            case {
                "type": "list" | "set" | "frozenset" | "generator",
                "items_schema": items_schema,
            }:
                schema["items_schema"] = self._process_nested_schema(items_schema)
            case _:
                pass
        return core_schema.no_info_wrap_validator_function(
            function=template_or_original_validator,
            schema=schema,
        )


class RequiredTemplateValidator:
    def __new__(cls):
        return WrapValidator(cls.must_expression)

    @classmethod
    def must_expression(
        cls, v: T, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> T:
        if isinstance(v, str) and not is_standalone_template(v):
            raise ValueError(f"'{v}' is not a valid expression")
        return handler(v)


ExpressionStr = Annotated[str, TemplateValidator()]
RequiredExpressionStr = Annotated[str, RequiredTemplateValidator()]
