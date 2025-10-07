import inspect
from typing import Any, get_args

import pytest
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_core import PydanticUndefined

from tracecat.agent.tools import (
    _create_function_signature,
    _extract_action_metadata,
    _generate_google_style_docstring,
)
from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.actions.models import (
    ActionStep,
    BoundRegistryAction,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.repository import Repository


class DummyField:
    """Minimal stub that mimics the parts of Pydantic's FieldInfo needed for tests."""

    def __init__(
        self,
        annotation: Any,
        *,
        default: Any = PydanticUndefined,
        default_factory: Any = None,
    ):
        self.annotation = annotation
        self.default = default
        self.default_factory = default_factory


class ExampleModel:
    """Stub model that provides Pydantic-like field metadata and schema information."""

    model_fields = {
        "required": DummyField(int),
        "with_default": DummyField(str, default="hello"),
        "with_factory": DummyField(list[int], default_factory=list),
        "already_optional": DummyField(int | None, default_factory=lambda: 42),
        "class": DummyField(bool),
    }

    @staticmethod
    def model_json_schema():
        return {
            "properties": {
                "required": {"description": "Required field"},
                "with_default": {"description": "Field with default"},
                "with_factory": {"description": "Generated value"},
                "already_optional": {},
                "class": {"description": "Keyword field"},
            }
        }


def test_create_function_signature_handles_defaults_and_sanitizes():
    result = _create_function_signature(ExampleModel, fixed_args={"with_default"})

    params = result.signature.parameters
    assert list(params) == ["required", "with_factory", "already_optional", "class_"]

    required = params["required"]
    assert required.kind is inspect.Parameter.KEYWORD_ONLY
    assert required.annotation is int
    assert required.default is inspect.Parameter.empty

    with_factory = params["with_factory"]
    assert with_factory.default is None
    assert with_factory.kind is inspect.Parameter.KEYWORD_ONLY
    assert with_factory.annotation == result.annotations["with_factory"]
    assert get_args(with_factory.annotation) == (list[int], type(None))

    already_optional = params["already_optional"]
    assert already_optional.default is None
    assert already_optional.annotation == int | None

    class_param = params["class_"]
    assert class_param.annotation is bool
    assert class_param.default is inspect.Parameter.empty

    assert "with_default" not in params
    assert result.annotations["return"] is Any
    assert result.param_mapping["class_"] == "class"


def test_generate_docstring_includes_schema_descriptions_and_skips_fixed_args():
    docstring = _generate_google_style_docstring(
        "Do something useful", ExampleModel, fixed_args={"with_default"}
    )

    expected = "\n".join(
        [
            "Do something useful",
            "",
            "Args:",
            "    required: Required field",
            "    with_factory: Generated value",
            "    already_optional: Parameter already_optional",
            "    class: Keyword field",
        ]
    )

    assert docstring == expected


def test_generate_docstring_returns_none_section_when_no_parameters():
    class EmptyModel:
        @staticmethod
        def model_json_schema():
            return {}

    docstring = _generate_google_style_docstring(
        "No parameters here", EmptyModel, fixed_args=set()
    )
    assert docstring == "No parameters here\n\nArgs:\n    None"


def test_generate_docstring_raises_when_description_missing():
    with pytest.raises(ValueError):
        _generate_google_style_docstring(None, ExampleModel)


class SampleArgs(BaseModel):
    foo: int = Field(..., description="Foo argument")


def sample_udf(foo: int) -> int:
    return foo


def build_udf_action(
    description: str = "Sample UDF description",
) -> BoundRegistryAction:
    repo = Repository()
    repo.register_udf(
        fn=sample_udf,
        name="sample_udf",
        type="udf",
        namespace="test",
        description=description,
        secrets=None,
        args_cls=SampleArgs,
        args_docs={"foo": "Foo argument"},
        rtype=int,
        rtype_adapter=TypeAdapter(int),
        default_title=None,
        display_group=None,
        doc_url=None,
        author="Tracecat",
        deprecated=None,
        include_in_schema=True,
    )
    return repo.get("test.sample_udf")


def build_template_action(
    *,
    template_description: str = "Template action description",
    expects_override: dict[str, ExpectedField] | None = None,
) -> BoundRegistryAction:
    repo = Repository()
    expects = expects_override or {
        "user_id": ExpectedField(type="int", description="User identifier"),
        "message": ExpectedField(type="str", description="Message to send"),
    }
    template_def = TemplateActionDefinition(
        name="send_message",
        namespace="templates",
        title="Send Message",
        description=template_description,
        display_group="Messaging",
        doc_url="https://example.com",
        author="Tracecat",
        deprecated=None,
        secrets=None,
        expects=expects,
        steps=[
            ActionStep(
                ref="first",
                action="test.sample_udf",
                args={"foo": 1},
            )
        ],
        returns="result",
    )
    template_action = TemplateAction(type="action", definition=template_def)
    repo.register_udf(
        fn=sample_udf,
        name="sample_udf",
        type="udf",
        namespace="test",
        description="Sample UDF description",
        secrets=None,
        args_cls=SampleArgs,
        args_docs={"foo": "Foo argument"},
        rtype=int,
        rtype_adapter=TypeAdapter(int),
        default_title=None,
        display_group=None,
        doc_url=None,
        author="Tracecat",
        deprecated=None,
        include_in_schema=True,
    )
    repo.register_template_action(template_action)
    return repo.get("templates.send_message")


def test_extract_action_metadata_udf_returns_description_and_args_model():
    bound_action = build_udf_action()
    description, model_cls = _extract_action_metadata(bound_action)

    assert description == "Sample UDF description"
    assert model_cls is SampleArgs


def test_extract_action_metadata_template_uses_template_description():
    bound_action = build_template_action()
    description, model_cls = _extract_action_metadata(bound_action)

    assert description == "Template action description"
    assert issubclass(model_cls, BaseModel)
    assert set(model_cls.model_fields) == {"user_id", "message"}
    assert model_cls.model_fields["user_id"].annotation is int


def test_extract_action_metadata_template_falls_back_to_bound_description():
    bound_action = build_template_action(template_description="")
    bound_action.description = "Fallback description"
    assert bound_action.template_action is not None
    bound_action.template_action.definition.description = ""

    description, _ = _extract_action_metadata(bound_action)
    assert description == "Fallback description"


def test_extract_action_metadata_template_without_template_action_raises():
    bound_action = BoundRegistryAction(
        fn=sample_udf,
        name="template_without_body",
        namespace="tests",
        description="Template missing body",
        type="template",
        origin="unit-test",
        secrets=None,
        args_cls=SampleArgs,
        args_docs={"foo": "Foo argument"},
        rtype_cls=int,
        rtype_adapter=TypeAdapter(int),
        default_title=None,
        display_group=None,
        doc_url=None,
        author="Tracecat",
        deprecated=None,
        template_action=None,
        include_in_schema=True,
    )

    with pytest.raises(ValueError):
        _extract_action_metadata(bound_action)
