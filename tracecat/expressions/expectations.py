from datetime import datetime, timedelta
from typing import Any, Union

from lark import Lark, Transformer, v_args
from pydantic import BaseModel, ConfigDict, Field, create_model

from tracecat.logger import logger

# Define the Lark grammar for parsing types
type_grammar = r"""
?type: primitive_type
     | list_type
     | dict_type
     | union_type
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
BOOLEAN: "bool"
FLOAT: "float"
DATETIME: "datetime"
DURATION: "duration"
ANY: "any"
NULL: "None"

list_type: "list" "[" type "]"
dict_type: "dict" "[" type "," type "]"
union_type: type ("|" type)+
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
    "None": None,
}


class TypeTransformer(Transformer):
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
    def dict_type(self, key_type, value_type) -> type:
        logger.trace("Dict type:", key_type=key_type, value_type=value_type)
        return dict[key_type, value_type]

    @v_args(inline=True)
    def union_type(self, *types) -> type:
        logger.trace("Union type:", types=types)
        return Union[types]  # noqa: UP007

    @v_args(inline=True)
    def reference_type(self, name) -> str:
        logger.trace("Reference type:", name=name)
        return f"${name.value}"


def parse_type(type_string: str) -> Any:
    tree = type_parser.parse(type_string)
    return TypeTransformer().transform(tree)


class ExpectedField(BaseModel):
    type: str
    description: str | None = None
    default: Any | None = None


def create_expectation_model(
    schema: dict[str, ExpectedField], model_name: str = "ExpectedSchemaModel"
) -> type[BaseModel]:
    fields = {}
    field_info_kwargs = {}
    for field_name, field_info in schema.items():
        validated_field_info = ExpectedField.model_validate(field_info)  # Defensive
        field_type = parse_type(validated_field_info.type)
        if validated_field_info.description:
            field_info_kwargs["description"] = validated_field_info.description
        field_info_kwargs["default"] = (
            validated_field_info.default
            if "default" in validated_field_info.model_fields_set
            else ...
        )

        fields[field_name] = (
            field_type,
            Field(**field_info_kwargs),
        )

    logger.trace("Creating expectation model", model_name=model_name, fields=fields)
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True),
        **fields,
    )
