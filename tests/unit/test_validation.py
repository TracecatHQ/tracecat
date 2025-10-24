from pathlib import Path
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ValidationError
from tracecat_registry import registry
from typing_extensions import Doc

# Add imports for expression validation
from tracecat.expressions.validation import TemplateValidator
from tracecat.registry.actions.models import TemplateAction
from tracecat.registry.actions.service import validate_action_template
from tracecat.registry.repository import Repository
from tracecat.types.exceptions import RegistryValidationError


def test_template_validator():
    class MyModel(BaseModel):
        my_action: Annotated[list[str], TemplateValidator()]

    # Sanity check
    model = MyModel(my_action=["hello", "world"])
    assert model.my_action == ["hello", "world"]

    model = MyModel(my_action="${{ my_list }}")  # type: ignore
    assert model.my_action == "${{ my_list }}"


def test_validator_function_wrap_handler():
    """This tests the UDF.validate_args method, which shouldn't raise any exceptions
    when given a templated expression.
    """
    # Register UDFs from the mock package
    repo = Repository()

    @registry.register(
        description="This is a test function",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f1(
        num: Annotated[
            int,
            Doc("This is a test number"),
        ],
    ) -> int:
        return num

    # Attaches TemplateValidator to the UDF
    repo._register_udf_from_function(f1, name="f1")

    # Test the registered UDF
    udf = repo.get("test.f1")
    udf.validate_args(args={"num": "${{ path.to.number }}"})
    udf.validate_args(args={"num": 1})

    @registry.register(
        description="This is a test function",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f2(
        obj: Annotated[
            dict[str, list[str]],
            Doc("This is a test dict of list of strings"),
        ],
    ) -> Any:
        return obj["a"]

    repo._register_udf_from_function(f2, name="f2")
    udf2 = repo.get("test.f2")

    # Test the UDF with an invalid object
    with pytest.raises(RegistryValidationError):
        udf2.validate_args(args={"obj": {"a": "not a list"}})

    # Should not raise
    udf2.validate_args(args={"obj": {"a": "${{ not a list }}"}})

    @registry.register(
        description="This is a test function",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f3(
        obj: Annotated[
            list[dict[str, int]],
            Doc("This is a test list of dicts"),
        ],
    ) -> Any:
        return obj[0]

    repo._register_udf_from_function(f3, name="f3")
    udf3 = repo.get("test.f3")

    # Should not raise
    udf3.validate_args(args={"obj": [{"a": 1}]})
    x = udf3.args_cls.model_validate({"obj": [{"a": 1}]})
    assert x.model_dump(warnings=True) == {"obj": [{"a": 1}]}
    udf3.validate_args(args={"obj": [{"a": "${{ a number }}"}]})
    x = udf3.args_cls.model_validate({"obj": [{"a": "${{ a number }}"}]})
    assert x.model_dump(warnings=True) == {"obj": [{"a": "${{ a number }}"}]}
    udf3.validate_args(args={"obj": ["${{ a number }}", {"a": "${{ a number }}"}]})
    x = udf3.args_cls.model_validate(
        {"obj": ["${{ a number }}", {"a": "${{ a number }}"}]}
    )
    assert x.model_dump(warnings=True) == {
        "obj": ["${{ a number }}", {"a": "${{ a number }}"}]
    }

    # Should raise
    with pytest.raises(RegistryValidationError):
        udf3.validate_args(args={"obj": ["string"]})

    with pytest.raises(RegistryValidationError):
        udf3.validate_args(args={"obj": [{"a": "string"}]})

    # Test deeply nested types
    @registry.register(
        description="Test function with deeply nested types",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f4(
        complex_obj: Annotated[
            dict[str, list[dict[str, list[dict[str, int]]]]],
            Doc("A deeply nested structure"),
        ],
    ) -> Any:
        return complex_obj

    repo._register_udf_from_function(f4, name="f4")
    udf4 = repo.get("test.f4")

    # Valid nested structure
    valid_obj = {"level1": [{"level2": [{"level3": 1}, {"level3": 2}]}]}
    udf4.validate_args(args={"complex_obj": valid_obj})

    # Valid with template expressions
    template_obj = {"level1": [{"level2": "${{ template.level2 }}"}]}
    udf4.validate_args(args={"complex_obj": template_obj})

    template_obj = {"level1": [{"level2": [{"level3": "${{ template.level3 }}"}]}]}
    udf4.validate_args(args={"complex_obj": template_obj})

    # Invalid nested structure
    with pytest.raises(RegistryValidationError):
        invalid_obj = {"level1": [{"level2": [{"level3": "not an int"}]}]}
        udf4.validate_args(args={"complex_obj": invalid_obj})

    @registry.register(
        description="Test function with tuple and set types",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f5(
        nested_collections: Annotated[
            dict[str, tuple[set[int], list[dict[str, set[str]]]]],
            Doc("Complex nested collections"),
        ],
    ) -> Any:
        return nested_collections

    repo._register_udf_from_function(f5, name="f5")
    udf5 = repo.get("test.f5")

    # Valid nested collections
    valid_collections = {"data": ({1, 2, 3}, [{"strings": {"a", "b", "c"}}])}
    udf5.validate_args(args={"nested_collections": valid_collections})

    # Valid with templates
    template_collections = {
        "data": ("${{ template.numbers }}", [{"strings": "${{ template.strings }}"}])
    }
    udf5.validate_args(args={"nested_collections": template_collections})

    # Invalid collections
    with pytest.raises(RegistryValidationError):
        invalid_collections = {
            "data": (
                {"not", "integers"},  # Should be set of ints
                [{"strings": {1, 2, 3}}],  # Should be set of strings
            )
        }
        udf5.validate_args(args={"nested_collections": invalid_collections})


@pytest.mark.anyio
async def test_invalid_template_validation():
    """Test that invalid templates are caught by the validation system."""

    # Initialize repository with base actions
    repo = Repository()
    repo.init(include_base=True, include_templates=False)

    # Test templates with validation errors
    invalid_templates_dir = Path("tests/data/templates/invalid")

    # Test invalid function template
    invalid_func_path = invalid_templates_dir / "invalid_function.yml"
    assert invalid_func_path.exists(), f"Test fixture missing: {invalid_func_path}"
    action = TemplateAction.from_yaml(invalid_func_path)
    repo.register_template_action(action)
    bound_action = repo.get("tools.test.test_invalid_function")
    errors = await validate_action_template(bound_action, repo)

    # Should have errors for unknown function and wrong argument count
    assert len(errors) > 0
    error_messages = [detail for err in errors for detail in err.details]
    assert any(
        "Unknown function name 'does_not_exist'" in msg for msg in error_messages
    )
    assert any("expects at least" in msg for msg in error_messages)

    # Test wrong arity template
    wrong_arity_path = invalid_templates_dir / "wrong_arity.yml"
    assert wrong_arity_path.exists(), f"Test fixture missing: {wrong_arity_path}"
    action = TemplateAction.from_yaml(wrong_arity_path)
    repo.register_template_action(action)
    bound_action = repo.get("tools.test.test_wrong_arity")
    errors = await validate_action_template(bound_action, repo)

    # Should have errors for wrong argument counts
    assert len(errors) > 0
    error_messages = [detail for err in errors for detail in err.details]
    assert any(
        "accepts at most" in msg for msg in error_messages
    )  # uppercase with 2 args
    assert any("expects at least" in msg for msg in error_messages)  # join with 0 args
    assert any("accepts at most" in msg for msg in error_messages)  # now with 1 arg

    # Test nonexistent action template
    nonexistent_action_path = invalid_templates_dir / "nonexistent_action.yml"
    assert nonexistent_action_path.exists(), (
        f"Test fixture missing: {nonexistent_action_path}"
    )
    action = TemplateAction.from_yaml(nonexistent_action_path)
    repo.register_template_action(action)
    bound_action = repo.get("tools.test.test_nonexistent_action")
    errors = await validate_action_template(bound_action, repo)

    # Should have error for action not found
    assert len(errors) > 0
    error_messages = [detail for err in errors for detail in err.details]
    assert any("not found" in msg for msg in error_messages)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "file_name,expected_error_pattern",
    [
        ("missing_title.yml", r"title\s+Field required"),
        ("missing_display_group.yml", r"display_group\s+Field required"),
        ("bad_expression_syntax.yml", r"Unexpected token.*'or'"),
        (
            "bad_jsonpath.yml",
            r"Unknown function name 'does_not_exist_func'|expects at least",
        ),
        ("unmatched_parens.yml", r"Unexpected token.*\$END"),
    ],
)
async def test_template_validation_errors(file_name, expected_error_pattern):
    """Test various template validation error scenarios."""
    import re

    invalid_templates_dir = Path("tests/data/templates/invalid")
    template_path = invalid_templates_dir / file_name

    # Some errors occur during parsing (Pydantic validation)
    if file_name in ("missing_title.yml", "missing_display_group.yml"):
        with pytest.raises(ValidationError) as exc_info:
            TemplateAction.from_yaml(template_path)

        error_str = str(exc_info.value)
        assert re.search(expected_error_pattern, error_str, re.IGNORECASE), (
            f"Expected pattern '{expected_error_pattern}' not found in error: {error_str}"
        )
    else:
        # Other errors occur during template validation
        repo = Repository()
        repo.init(include_base=True, include_templates=False)

        action = TemplateAction.from_yaml(template_path)
        repo.register_template_action(action)
        bound_action = repo.get(action.definition.action)
        errors = await validate_action_template(bound_action, repo)

        assert len(errors) > 0, f"Expected validation errors for {file_name}"

        # Check if any error message matches the expected pattern
        all_error_messages = []
        for err in errors:
            all_error_messages.extend(err.details)

        error_text = " ".join(all_error_messages)
        assert re.search(expected_error_pattern, error_text, re.IGNORECASE), (
            f"Expected pattern '{expected_error_pattern}' not found in errors: {all_error_messages}"
        )
