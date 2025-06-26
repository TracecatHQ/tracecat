from datetime import datetime
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from tracecat.validation.common import json_schema_to_pydantic


def test_simple_schema():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # Check for Annotated[str, ...]
    name_type_annotation = Model.__annotations__["name"]
    assert get_origin(name_type_annotation) is Annotated
    assert get_args(name_type_annotation)[0] is str

    # Check for Optional[Annotated[int, ...]]
    age_type_annotation = Model.__annotations__["age"]
    assert get_origin(age_type_annotation) is Union
    age_args = get_args(age_type_annotation)
    assert type(None) in age_args
    annotated_int_type = next(arg for arg in age_args if arg is not type(None))
    assert get_origin(annotated_int_type) is Annotated
    assert get_args(annotated_int_type)[0] is int


def test_nested_object():
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # Check for Optional[Annotated[NestedModel, ...]]
    user_type_annotation = Model.__annotations__["user"]
    assert get_origin(user_type_annotation) is Union
    user_args = get_args(user_type_annotation)
    assert type(None) in user_args
    annotated_user_type = next(arg for arg in user_args if arg is not type(None))
    assert get_origin(annotated_user_type) is Annotated
    NestedUserModel = get_args(annotated_user_type)[0]
    assert issubclass(NestedUserModel, BaseModel)

    # Check types within the nested model
    # name is Optional[Annotated[str, ...]] because it's not in 'required' of the nested object
    name_type_annotation_nested = NestedUserModel.__annotations__["name"]
    assert get_origin(name_type_annotation_nested) is Union
    name_args_nested = get_args(name_type_annotation_nested)
    assert type(None) in name_args_nested
    annotated_str_type_nested = next(
        arg for arg in name_args_nested if arg is not type(None)
    )
    assert get_origin(annotated_str_type_nested) is Annotated
    assert get_args(annotated_str_type_nested)[0] is str

    # age is Optional[Annotated[int, ...]]
    age_type_annotation_nested = NestedUserModel.__annotations__["age"]
    assert get_origin(age_type_annotation_nested) is Union
    age_args_nested = get_args(age_type_annotation_nested)
    assert type(None) in age_args_nested
    annotated_int_type_nested = next(
        arg for arg in age_args_nested if arg is not type(None)
    )
    assert get_origin(annotated_int_type_nested) is Annotated
    assert get_args(annotated_int_type_nested)[0] is int


def test_array_field():
    schema = {
        "type": "object",
        "properties": {"numbers": {"type": "array", "items": {"type": "integer"}}},
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # Check for Optional[Annotated[list[int], ...]]
    numbers_type_annotation = Model.__annotations__["numbers"]
    assert get_origin(numbers_type_annotation) is Union
    numbers_args = get_args(numbers_type_annotation)
    assert type(None) in numbers_args
    annotated_list_type = next(arg for arg in numbers_args if arg is not type(None))
    assert get_origin(annotated_list_type) is Annotated
    list_type_args = get_args(annotated_list_type)
    assert list_type_args[0].__origin__ is list  # Check that it's a list
    assert list_type_args[0].__args__[0] is int  # Check that list items are int


def test_reference():
    schema = {
        "type": "object",
        "properties": {"address": {"$ref": "#/definitions/Address"}},
        "definitions": {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # Check for Optional[Annotated[AddressModel, ...]]
    address_type_annotation = Model.__annotations__["address"]
    assert get_origin(address_type_annotation) is Union
    address_args = get_args(address_type_annotation)
    assert type(None) in address_args
    annotated_address_type = next(arg for arg in address_args if arg is not type(None))
    assert get_origin(annotated_address_type) is Annotated
    AddressModel = get_args(annotated_address_type)[0]
    assert issubclass(AddressModel, BaseModel)

    # Check types within the referenced model
    # street is Optional[Annotated[str, ...]]
    street_type_annotation = AddressModel.__annotations__["street"]
    assert get_origin(street_type_annotation) is Union
    street_args = get_args(street_type_annotation)
    assert type(None) in street_args
    annotated_str_type_street = next(
        arg for arg in street_args if arg is not type(None)
    )
    assert get_origin(annotated_str_type_street) is Annotated
    assert get_args(annotated_str_type_street)[0] is str
    # city is Optional[Annotated[str, ...]]
    city_type_annotation = AddressModel.__annotations__["city"]
    assert get_origin(city_type_annotation) is Union
    city_args = get_args(city_type_annotation)
    assert type(None) in city_args
    annotated_str_type_city = next(arg for arg in city_args if arg is not type(None))
    assert get_origin(annotated_str_type_city) is Annotated
    assert get_args(annotated_str_type_city)[0] is str


def test_required_fields():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name"],
    }

    Model = json_schema_to_pydantic(schema)

    # name is Annotated[str, ...] because it's required
    name_type_annotation = Model.__annotations__["name"]
    assert get_origin(name_type_annotation) is Annotated
    assert get_args(name_type_annotation)[0] is str

    # age is Optional[Annotated[int, ...]] because it's not required
    age_type_annotation = Model.__annotations__["age"]
    assert get_origin(age_type_annotation) is Union
    age_args = get_args(age_type_annotation)
    assert type(None) in age_args
    annotated_int_type = next(arg for arg in age_args if arg is not type(None))
    assert get_origin(annotated_int_type) is Annotated
    assert get_args(annotated_int_type)[0] is int


def test_field_description():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The user's name"}},
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # name is Optional[Annotated[str, ...]] because it's not required
    name_type_annotation = Model.__annotations__["name"]
    assert get_origin(name_type_annotation) is Union
    name_args = get_args(name_type_annotation)
    assert type(None) in name_args
    annotated_name_type = next(arg for arg in name_args if arg is not type(None))
    assert get_origin(annotated_name_type) is Annotated
    assert get_args(annotated_name_type)[0] is str
    assert Model.model_fields["name"].description == "The user's name"


def test_complex_schema():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "is_student": {"type": "boolean"},
            "grades": {"type": "array", "items": {"type": "number"}},
            "address": {"$ref": "#/definitions/Address"},
        },
        "required": ["name", "age"],
        "definitions": {
            "Address": {
                "type": "object",
                "properties": {
                    "street": {"type": "string"},
                    "city": {"type": "string"},
                },
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # name: Annotated[str, ...] (required)
    name_type_annotation = Model.__annotations__["name"]
    assert get_origin(name_type_annotation) is Annotated
    assert get_args(name_type_annotation)[0] is str

    # age: Annotated[int, ...] (required)
    age_type_annotation = Model.__annotations__["age"]
    assert get_origin(age_type_annotation) is Annotated
    assert get_args(age_type_annotation)[0] is int

    # is_student: Optional[Annotated[bool, ...]]
    is_student_type_annotation = Model.__annotations__["is_student"]
    assert get_origin(is_student_type_annotation) is Union
    is_student_args = get_args(is_student_type_annotation)
    assert type(None) in is_student_args
    annotated_bool_type = next(arg for arg in is_student_args if arg is not type(None))
    assert get_origin(annotated_bool_type) is Annotated
    assert get_args(annotated_bool_type)[0] is bool

    # grades: Optional[Annotated[list[float], ...]]
    grades_type_annotation = Model.__annotations__["grades"]
    assert get_origin(grades_type_annotation) is Union
    grades_args = get_args(grades_type_annotation)
    assert type(None) in grades_args
    annotated_list_float_type = next(
        arg for arg in grades_args if arg is not type(None)
    )
    assert get_origin(annotated_list_float_type) is Annotated
    list_float_args = get_args(annotated_list_float_type)
    assert list_float_args[0].__origin__ is list
    assert list_float_args[0].__args__[0] is float

    # address: Optional[Annotated[AddressModel, ...]]
    address_type_annotation = Model.__annotations__["address"]
    assert get_origin(address_type_annotation) is Union
    address_args = get_args(address_type_annotation)
    assert type(None) in address_args
    annotated_address_type = next(arg for arg in address_args if arg is not type(None))
    assert get_origin(annotated_address_type) is Annotated
    AddressModel = get_args(annotated_address_type)[0]
    assert issubclass(AddressModel, BaseModel)


def test_invalid_schema():
    schema = {"type": "object", "properties": {"field_a": {"type": "invalid_type"}}}
    Model = json_schema_to_pydantic(schema)
    # field_a should be Optional[Annotated[Any, TemplateValidator()]]
    field_a_type_annotation = Model.__annotations__["field_a"]
    assert get_origin(field_a_type_annotation) is Union
    field_a_args = get_args(field_a_type_annotation)
    assert type(None) in field_a_args
    annotated_any_type = next(arg for arg in field_a_args if arg is not type(None))
    assert get_origin(annotated_any_type) is Annotated
    assert get_args(annotated_any_type)[0] is Any


def test_enum_fields():
    schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "closed"],
                "title": "ticket_status",
            },
            "severity": {
                "type": "integer",
                "enum": [1, 2, 3, 4],
            },
        },
        "required": ["status"],
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)

    # Check status enum (required)
    status_type_annotation = Model.__annotations__["status"]
    assert get_origin(status_type_annotation) is Annotated
    StatusLiteralType = get_args(status_type_annotation)[0]
    assert get_origin(StatusLiteralType) is Literal
    assert set(get_args(StatusLiteralType)) == {"open", "in_progress", "closed"}

    # Check severity enum (optional)
    severity_type_annotation = Model.__annotations__["severity"]
    assert get_origin(severity_type_annotation) is Union  # For Optional fields
    severity_args = get_args(severity_type_annotation)
    assert type(None) in severity_args
    annotated_severity_type = next(
        arg for arg in severity_args if arg is not type(None)
    )
    assert get_origin(annotated_severity_type) is Annotated
    SeverityLiteralType = get_args(annotated_severity_type)[0]
    assert get_origin(SeverityLiteralType) is Literal
    assert set(get_args(SeverityLiteralType)) == {1, 2, 3, 4}


def test_array_of_objects():
    schema = {
        "type": "object",
        "properties": {
            "items_list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "title": "ListItem",
                    "properties": {
                        "item_id": {"type": "integer"},
                        "item_name": {"type": "string"},
                    },
                    "required": ["item_id", "item_name"],
                },
            }
        },
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)
    # Check that items_list is Optional[Annotated[list[ListItemModel], ...]]
    items_list_annotation = Model.__annotations__["items_list"]
    assert get_origin(items_list_annotation) is Union
    items_list_args = get_args(items_list_annotation)
    assert type(None) in items_list_args
    annotated_list_type = next(arg for arg in items_list_args if arg is not type(None))
    assert get_origin(annotated_list_type) is Annotated

    list_of_listitem_type = get_args(annotated_list_type)[
        0
    ]  # This should be list[ListItemModel]
    assert get_origin(list_of_listitem_type) is list

    # Get the nested ListItem model
    ListItemModel = get_args(list_of_listitem_type)[0]
    assert issubclass(ListItemModel, BaseModel)
    assert ListItemModel.__name__ == "ListItem"

    # Check properties of the nested ListItemModel
    # item_id is Annotated[int, ...] because it's required in ListItem
    item_id_annotation = ListItemModel.__annotations__["item_id"]
    assert get_origin(item_id_annotation) is Annotated
    assert get_args(item_id_annotation)[0] is int

    # item_name is Annotated[str, ...] because it's required in ListItem
    item_name_annotation = ListItemModel.__annotations__["item_name"]
    assert get_origin(item_name_annotation) is Annotated
    assert get_args(item_name_annotation)[0] is str


def test_datetime_format():
    """Test that string fields with 'date-time' format are properly converted to datetime type."""
    schema = {
        "type": "object",
        "title": "DateTimeTest",
        "properties": {
            "start": {
                "type": "string",
                "format": "date-time",
                "description": "The start date.",
            },
            "end": {
                "type": "string",
                "format": "date-time",
                "description": "The end date.",
            },
            "regular_string": {
                "type": "string",
                "description": "A regular string field.",
            },
        },
        "required": ["start", "end"],
    }

    Model = json_schema_to_pydantic(schema)

    assert issubclass(Model, BaseModel)

    # start is Annotated[datetime, ...] because it's required and has date-time format
    start_type_annotation = Model.__annotations__["start"]
    assert get_origin(start_type_annotation) is Annotated
    assert get_args(start_type_annotation)[0] is datetime

    # end is Annotated[datetime, ...] because it's required and has date-time format
    end_type_annotation = Model.__annotations__["end"]
    assert get_origin(end_type_annotation) is Annotated
    assert get_args(end_type_annotation)[0] is datetime

    # regular_string is Optional[Annotated[str, ...]] because it's not required and no format
    regular_string_type_annotation = Model.__annotations__["regular_string"]
    assert get_origin(regular_string_type_annotation) is Union
    regular_string_args = get_args(regular_string_type_annotation)
    assert type(None) in regular_string_args
    annotated_str_type = next(
        arg for arg in regular_string_args if arg is not type(None)
    )
    assert get_origin(annotated_str_type) is Annotated
    assert get_args(annotated_str_type)[0] is str

    # Test validation with datetime objects
    test_data = {
        "start": datetime(2025, 6, 26, 15, 51, 8, 346252),
        "end": datetime(2025, 6, 26, 16, 51, 8, 346252),
    }

    validated = Model.model_validate(test_data)
    assert isinstance(validated.start, datetime)  # type: ignore
    assert isinstance(validated.end, datetime)  # type: ignore

    # Test validation with string inputs
    test_data_str = {
        "start": "2025-06-26T15:51:08.346252",
        "end": "2025-06-26T16:51:08.346252",
    }

    validated_str = Model.model_validate(test_data_str)
    assert isinstance(validated_str.start, datetime)  # type: ignore
    assert isinstance(validated_str.end, datetime)  # type: ignore
