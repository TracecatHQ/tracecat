from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Literal, Union

from lark import Lark, Transformer, v_args
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_core import to_jsonable_python

from tracecat.expressions.schemas import ExpectedField
from tracecat.logger import logger
from tracecat.registry.fields import get_components_for_union_type, type_drop_null

# Re-export for backwards compatibility
__all__ = ["ExpectedField", "create_expectation_model"]

type_grammar = r"""
?type: primitive_type
     | list_type
     | dict_type
     | union_type
     | enum_type
     | reference_type

primitive_type: INTEGER
     | STRING
     | BOOLEAN
     | FLOAT
     | DATETIME
     | DURATION
     | ANY
     | NULL

INTEGER: "int"
STRING: "str"
STRING_LITERAL: "\"" /[^"]*/ "\"" | "'" /[^']*/ "'"
BOOLEAN: "bool"
FLOAT: "float"
DATETIME: "datetime"
DURATION: "duration"
# Allow both lowercase and capitalized variant for the primitive "any"
ANY: "any" | "Any"
NULL: "None"

list_type: "list" "[" type "]"
dict_type: "dict" ("[" type "," type "]")?
union_type: type ("|" type)+
enum_type: "enum" "[" STRING_LITERAL ("," STRING_LITERAL)* "]"
reference_type: "$" CNAME

CNAME: /[a-zA-Z_]\w*/

%import common.WS
%ignore WS
"""

# Create a Lark parser
type_parser = Lark(type_grammar, start="type")

TYPE_MAPPING = {
    "int": int,
    "str": str,
    "bool": bool,
    "float": float,
    "datetime": datetime,
    "duration": timedelta,
    "any": Any,
    "Any": Any,  # Alias for any (preferred over any in templates)
    "None": None,
}


class TypeTransformer(Transformer):
    MAX_ENUM_VALUES = 20

    def __init__(self, field_name: str):
        super().__init__()
        self.field_name = field_name

    @v_args(inline=True)
    def primitive_type(self, item) -> type | None:
        logger.trace("Primitive type:", item=item)
        if item in TYPE_MAPPING:
            return TYPE_MAPPING[item]
        else:
            raise ValueError(f"Unknown primitive type: {item}")

    @v_args(inline=True)
    def list_type(self, item_type) -> type:
        logger.trace("List type:", item_type=item_type)
        return list[item_type]

    @v_args(inline=True)
    def dict_type(self, *types) -> type:
        if not types:
            logger.trace("Dict type:", key_type="Any", value_type="Any (implicit)")
            return dict[str, Any]

        if len(types) != 2:
            raise ValueError("Dict type must have either zero or two type arguments")

        key_type, value_type = types
        logger.trace("Dict type:", key_type=key_type, value_type=value_type)
        return dict[key_type, value_type]

    @v_args(inline=True)
    def union_type(self, *types) -> type:
        logger.trace("Union type:", types=types)
        return Union[types]  # noqa: UP007  # pyright: ignore[reportReturnType]

    @v_args(inline=True)
    def reference_type(self, name) -> str:
        logger.trace("Reference type:", name=name)
        return f"${name.value}"

    @v_args(inline=True)
    def enum_type(self, *values) -> Any:
        if len(values) > self.MAX_ENUM_VALUES:
            raise ValueError(f"Too many enum values (maximum {self.MAX_ENUM_VALUES})")

        literal_values: list[str] = []
        seen_values = set()

        for value in values:
            if not value:
                raise ValueError("Enum value cannot be empty")

            # Case-insensitive duplicate check
            value_lower = value.lower()
            if value_lower in seen_values:
                raise ValueError(f"Duplicate enum value: {value}")

            seen_values.add(value_lower)
            literal_values.append(value)

        literal_type = Literal.__getitem__(tuple(literal_values))  # pyright: ignore[reportAttributeAccessIssue]
        logger.trace("Enum literal type:", field=self.field_name, values=literal_values)
        return literal_type

    @v_args(inline=True)
    def STRING_LITERAL(self, value) -> str:
        # Remove quotes from the value
        value = str(value).strip('"').strip("'")
        return value


def parse_type(type_string: str, field_name: str) -> Any:
    tree = type_parser.parse(type_string)
    return TypeTransformer(field_name).transform(tree)


def create_expectation_model(
    schema: Mapping[str, ExpectedField | Mapping[str, Any]],
    model_name: str = "ExpectedSchemaModel",
) -> type[BaseModel]:
    fields = {}
    for field_name, field_info in schema.items():
        field_info_kwargs = {}
        # Defensive validation
        validated_field_info = ExpectedField.model_validate(field_info)

        # Extract metadata
        field_type: type = parse_type(validated_field_info.type, field_name)
        description = validated_field_info.description

        if description:
            field_info_kwargs["description"] = description

        if validated_field_info.has_default():
            # If the field has a default value, use it
            field_info_kwargs["default"] = validated_field_info.default
        else:
            # Use ... (ellipsis) to indicate a required field in Pydantic
            field_info_kwargs["default"] = ...

        # Handle Tracecat component annotations
        # Check if the field type has component annotations
        # Get the default UI for the field
        non_null_field_type = type_drop_null(field_type)
        components = get_components_for_union_type(non_null_field_type)

        # Add components to field metadata if they exist
        if components:
            jsonschema_extra = field_info_kwargs.setdefault("json_schema_extra", {})
            jsonschema_extra["x-tracecat-component"] = to_jsonable_python(components)

        field = Field(**field_info_kwargs)
        fields[field_name] = (field_type, field)

    logger.trace("Creating expectation model", model_name=model_name, fields=fields)
    model = create_model(
        model_name,
        __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True),
        **fields,
    )
    return model
