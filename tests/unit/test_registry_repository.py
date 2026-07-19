from __future__ import annotations

import importlib
import importlib.machinery
import inspect
import sys
from types import ModuleType
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Literal,
    TypedDict,
    cast,
    get_args,
    get_origin,
)

import pytest
from pydantic import ConfigDict, Field, ValidationError
from typing_extensions import Doc

from tracecat.expressions.validation import TemplateValidator
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.repository import (
    RegisterKwargs,
    attach_validators,
    generate_model_from_function,
    import_and_reload,
)
from tracecat.validation.common import json_schema_to_pydantic


def _metadata(annotation: object) -> list[object]:
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return list(args[1:])
    return []


def test_attach_validators_idempotent() -> None:
    def sample(x: int) -> int:  # noqa: ANN001 - test helper
        return x

    attach_validators(sample, TemplateValidator())
    attach_validators(sample, TemplateValidator())

    annotation = sample.__annotations__["x"]
    metas = _metadata(annotation)
    template_validators = [m for m in metas if isinstance(m, TemplateValidator)]

    assert len(template_validators) == 1
    if get_origin(annotation) is Annotated:
        base = get_args(annotation)[0]
        assert base in (int, "int", ForwardRef("int"))


def test_import_and_reload_falls_back_when_loader_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "tracecat._test_dummy_registry"
    dummy = ModuleType(module_name)
    dummy.__spec__ = importlib.machinery.ModuleSpec(name=module_name, loader=None)

    sentinel = ModuleType(module_name)
    sentinel.__spec__ = importlib.machinery.ModuleSpec(name=module_name, loader=None)
    calls: list[str] = []

    def fake_import(name: str) -> ModuleType:
        assert name == module_name
        calls.append(name)
        importlib.invalidate_caches()
        return sentinel

    monkeypatch.setitem(sys.modules, module_name, dummy)
    monkeypatch.setattr(
        "tracecat.registry.repository.importlib.import_module", fake_import
    )

    def boom(_: ModuleType) -> ModuleType:  # pragma: no cover - should not run
        raise AssertionError("reload should not have been called")

    monkeypatch.setattr("tracecat.registry.repository.importlib.reload", boom)

    try:
        result = import_and_reload(module_name)
        assert result is sentinel
        assert calls == [module_name]
    finally:
        sys.modules.pop(module_name, None)


# =============================================================================
# Tests for generate_model_from_function ForwardRef resolution
# =============================================================================


class SampleTypedDict(TypedDict):
    """Sample TypedDict for testing."""

    id: str
    name: str


def _make_kwargs() -> RegisterKwargs:
    return RegisterKwargs(
        namespace="test.namespace",
        default_title="Test",
        description="Test function",
        display_group="Test",
    )


def test_generate_model_resolves_typeddict_return_type() -> None:
    """Test that TypedDict return types are correctly resolved."""

    def sample_func(x: str) -> SampleTypedDict:
        return {"id": x, "name": x}

    _, rtype, rtype_adapter = generate_model_from_function(sample_func, _make_kwargs())

    assert rtype is SampleTypedDict
    # Verify JSON schema can be generated
    schema = rtype_adapter.json_schema()
    assert "properties" in schema
    assert "id" in schema["properties"]
    assert "name" in schema["properties"]


def test_generate_model_resolves_optional_typeddict_return_type() -> None:
    """Test that optional TypedDict return types (T | None) are resolved."""

    def sample_func(x: str) -> SampleTypedDict | None:
        return None

    _, rtype, _ = generate_model_from_function(sample_func, _make_kwargs())

    # Should be a union type containing SampleTypedDict and None
    origin = get_origin(rtype)
    assert origin is not None  # It's a Union type
    args = get_args(rtype)
    assert SampleTypedDict in args
    assert type(None) in args


def test_generate_model_preserves_literal_types_in_params() -> None:
    """Test that Literal types in parameters are preserved with their args."""

    def sample_func(status: Literal["pending", "active", "done"]) -> str:
        return status

    input_model, _, _ = generate_model_from_function(sample_func, _make_kwargs())

    # Get the field type from the model
    field_info = input_model.model_fields["status"]
    field_type = field_info.annotation

    # The Literal type should be preserved with its args
    assert get_origin(field_type) is Literal
    assert get_args(field_type) == ("pending", "active", "done")


def test_generate_model_extracts_base_type_from_annotated() -> None:
    """Test that Annotated[T, ...] correctly extracts T as base type."""

    def sample_func(
        name: Annotated[str, Doc("The name")],
        count: Annotated[int, Doc("The count")],
    ) -> str:
        return name

    input_model, _, _ = generate_model_from_function(sample_func, _make_kwargs())

    # Check that descriptions were extracted from Doc
    assert input_model.model_fields["name"].description == "The name"
    assert input_model.model_fields["count"].description == "The count"

    # Verify the model accepts correct types
    instance: Any = input_model(name="test", count=42)
    assert instance.name == "test"
    assert instance.count == 42


@pytest.mark.regression
def test_generate_model_honors_annotated_field_default_for_omitted_nullable_param() -> None:
    """Regression for #2620: Field(default=...) must shape the args model."""

    def sample_func(
        summary: Annotated[str, Field(..., description="Required summary")],
        client_id: Annotated[
            int | None, Field(default=None, description="Optional client")
        ],
        priority: Annotated[
            int, Field(default=3, ge=1, le=5, description="Priority")
        ],
    ) -> dict[str, str | int | None]:
        return {"summary": summary, "client_id": client_id, "priority": priority}

    assert (
        inspect.signature(sample_func).parameters["client_id"].default
        is inspect.Parameter.empty
    )
    with pytest.raises(TypeError):
        cast(Any, sample_func)(summary="Ticket without client_id")

    input_model, _, _ = generate_model_from_function(sample_func, _make_kwargs())
    schema = input_model.model_json_schema()
    summary_field = input_model.model_fields["summary"]
    client_id_field = input_model.model_fields["client_id"]
    priority_field = input_model.model_fields["priority"]

    assert summary_field.is_required()
    assert client_id_field.default is None
    assert not client_id_field.is_required()
    assert client_id_field.description == "Optional client"
    assert priority_field.default == 3
    assert not priority_field.is_required()
    assert priority_field.description == "Priority"
    assert "client_id" not in schema.get("required", [])
    assert schema["properties"]["client_id"]["default"] is None
    assert {"type": "integer"} in schema["properties"]["client_id"]["anyOf"]
    assert {"type": "null"} in schema["properties"]["client_id"]["anyOf"]
    assert "priority" not in schema.get("required", [])
    assert schema["properties"]["priority"]["default"] == 3

    args = input_model.model_validate({"summary": "Ticket without client_id"})
    assert args.model_dump() == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }

    with pytest.raises(ValidationError):
        input_model.model_validate({"summary": "x", "priority": 0})
    with pytest.raises(ValidationError):
        input_model.model_validate({"summary": "x", "client_id": "not-an-int"})

    result = sample_func(**args.model_dump())
    assert result == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }


@pytest.mark.regression
def test_generate_model_keeps_nullable_parameter_required_without_default() -> None:
    """Nullable means None is valid; it must not make an omitted value optional."""

    def sample_func(client_id: int | None) -> int | None:
        return client_id

    input_model, _, _ = generate_model_from_function(sample_func, _make_kwargs())
    schema = input_model.model_json_schema()

    assert "client_id" in schema["required"]
    with pytest.raises(ValidationError):
        input_model.model_validate({})
    assert input_model.model_validate({"client_id": None}).model_dump() == {
        "client_id": None
    }
    assert input_model.model_validate({"client_id": 123}).model_dump() == {
        "client_id": 123
    }


@pytest.mark.regression
def test_udf_interface_schema_roundtrip_preserves_annotated_field_defaults() -> None:
    """Regression for #2620: manifest validation must apply schema defaults."""

    def sample_func(
        summary: Annotated[str, Field(..., description="Required summary")],
        client_id: Annotated[
            int | None, Field(default=None, description="Optional client")
        ],
        priority: Annotated[int, Field(default=3, description="Priority")],
    ) -> dict[str, str | int | None]:
        return {"summary": summary, "client_id": client_id, "priority": priority}

    assert (
        inspect.signature(sample_func).parameters["client_id"].default
        is inspect.Parameter.empty
    )
    with pytest.raises(TypeError):
        cast(Any, sample_func)(summary="Ticket without client_id")

    input_model, rtype, rtype_adapter = generate_model_from_function(
        sample_func, _make_kwargs()
    )
    bound_action = BoundRegistryAction(
        fn=sample_func,
        type="udf",
        name="create_ticket",
        namespace="testing",
        description="Test",
        secrets=None,
        args_cls=input_model,
        args_docs={},
        rtype_cls=rtype,
        rtype_adapter=rtype_adapter,
        default_title=None,
        display_group=None,
        doc_url=None,
        author=None,
        deprecated=None,
        origin="test",
    )
    interface = bound_action.get_interface()
    expects = interface["expects"]
    required = expects.get("required", [])

    assert "client_id" not in required
    assert expects["properties"]["client_id"]["default"] is None
    assert {"type": "integer"} in expects["properties"]["client_id"]["anyOf"]
    assert {"type": "null"} in expects["properties"]["client_id"]["anyOf"]
    assert "priority" not in required
    assert expects["properties"]["priority"]["default"] == 3

    roundtrip_model = json_schema_to_pydantic(
        expects,
        root_config=ConfigDict(extra="forbid"),
    )
    validated = roundtrip_model.model_validate({"summary": "Ticket without client_id"})

    assert validated.model_dump() == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }

    with pytest.raises(ValidationError):
        roundtrip_model.model_validate({})
    with pytest.raises(ValidationError):
        roundtrip_model.model_validate(
            {"summary": "Ticket without client_id", "client_id": "not-an-int"}
        )


@pytest.mark.regression
def test_json_schema_to_pydantic_preserves_optional_field_defaults() -> None:
    """Regression for #2620: manifest validation must not replace defaults with None."""

    model = json_schema_to_pydantic(
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "client_id": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                },
                "priority": {"type": "integer", "default": 3},
            },
            "required": ["summary"],
        },
        root_config=ConfigDict(extra="forbid"),
    )

    validated = model.model_validate({"summary": "Ticket without client_id"})

    assert validated.model_dump() == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }

    with pytest.raises(ValidationError):
        model.model_validate(
            {"summary": "Ticket without client_id", "client_id": "not-an-int"}
        )


def test_generate_model_preserves_component_metadata() -> None:
    """Ensure `x-tracecat-component` propagates to JSON schema.

    Registry modules use lightweight dataclasses from `tracecat_registry.fields`
    which are not instances of `tracecat.registry.fields.Component` but should
    still propagate to JSON schema so the UI can render specialized editors.
    """
    import tracecat_registry.fields as registry_fields

    Code = registry_fields.Code

    def sample_func(  # noqa: ANN001 - test helper
        script: Annotated[str, Code(lang="python")],
    ) -> str:
        return script

    # Inject Code into the function's globals so pydantic can resolve it
    sample_func.__globals__["Code"] = Code
    input_model, _, _ = generate_model_from_function(sample_func, _make_kwargs())
    schema = input_model.model_json_schema()

    script_schema = schema["properties"]["script"]
    components = script_schema.get("x-tracecat-component", [])
    assert components and components[0]["component_id"] == "code"


def test_generate_model_handles_annotated_literal() -> None:
    """Test that Annotated[Literal[...], Doc(...)] works correctly."""

    def sample_func(
        priority: Annotated[Literal["low", "medium", "high"], Doc("Priority level")],
    ) -> str:
        return priority

    input_model, _, _ = generate_model_from_function(sample_func, _make_kwargs())

    # Check description was extracted
    assert input_model.model_fields["priority"].description == "Priority level"

    # Verify the model validates literal values
    instance: Any = input_model(priority="high")
    assert instance.priority == "high"


def test_generate_model_handles_list_return_type() -> None:
    """Test that list[TypedDict] return types are resolved."""

    def sample_func(x: str) -> list[SampleTypedDict]:
        return []

    _, rtype, _ = generate_model_from_function(sample_func, _make_kwargs())

    assert get_origin(rtype) is list
    args = get_args(rtype)
    assert len(args) == 1
    assert args[0] is SampleTypedDict


def test_generate_model_handles_dict_return_type() -> None:
    """Test that dict[str, Any] return types work correctly."""

    def sample_func(x: str) -> dict[str, Any]:
        return {}

    _, rtype, rtype_adapter = generate_model_from_function(sample_func, _make_kwargs())

    assert get_origin(rtype) is dict
    # Verify schema generation works
    schema = rtype_adapter.json_schema()
    assert schema["type"] == "object"


def test_generate_model_fallback_for_missing_return_type() -> None:
    """Test that missing return type falls back to Any."""

    def sample_func(x: str):
        return x

    _, rtype, _ = generate_model_from_function(sample_func, _make_kwargs())

    assert rtype is Any


# =============================================================================
# Tests for actual registry modules with `from __future__ import annotations`
# =============================================================================


def test_registry_module_with_future_annotations_resolves_types() -> None:
    """Test that registry modules using `from __future__ import annotations` work correctly.

    The table module uses `from __future__ import annotations` which makes all
    annotations strings (ForwardRefs). This test verifies that:
    1. The function can be processed without errors
    2. Input parameter types are correctly resolved
    3. Return types (including TypedDict) are correctly resolved
    4. JSON schemas can be generated
    """
    from tracecat_registry.core.table import lookup

    kwargs = RegisterKwargs(
        namespace="core.table",
        default_title="Lookup row",
        description="Test",
        display_group="Tables",
    )

    input_model, rtype, rtype_adapter = generate_model_from_function(lookup, kwargs)

    # Verify input model was created and has expected fields
    assert "table" in input_model.model_fields
    assert "column" in input_model.model_fields
    assert "value" in input_model.model_fields

    # Verify return type is resolved (not a string or ForwardRef)
    assert not isinstance(rtype, str)
    assert not isinstance(rtype, ForwardRef)

    # Verify JSON schema can be generated without errors
    schema = rtype_adapter.json_schema()
    assert schema is not None


def test_registry_module_literal_types_preserved() -> None:
    """Test that Literal types in registry modules are preserved with their args.

    This verifies that parameters with Literal types (like format options)
    are not stripped down to just `typing.Literal` without the actual values.
    """
    from tracecat_registry.core.table import download

    kwargs = RegisterKwargs(
        namespace="core.table",
        default_title="Download table",
        description="Test",
        display_group="Tables",
    )

    input_model, _, _ = generate_model_from_function(download, kwargs)

    # The 'format' parameter should be a Literal type with its options preserved
    format_field = input_model.model_fields["format"]
    format_annotation = format_field.annotation

    # Check it's a union type (Literal[...] | None)
    origin = get_origin(format_annotation)
    assert origin is not None
    args = get_args(format_annotation)
    # Find the Literal type in the union
    literal_types = [arg for arg in args if get_origin(arg) is Literal]
    assert literal_types
    literal_args = get_args(literal_types[0])
    # Verify the literal values are present
    assert "json" in literal_args
    assert "csv" in literal_args
