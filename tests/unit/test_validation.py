import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import pytest
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import RegistryOAuthSecret, registry
from typing_extensions import Doc

from tracecat import config
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    create_default_execution_context,
)
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import (
    ExecutionError,
    RegistryValidationError,
)
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.service import dispatch_action
from tracecat.expressions.expectations import ExpectedField

# Add imports for expression validation
from tracecat.expressions.validation import TemplateValidator
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.schemas import (
    ActionStep,
    RegistryActionCreate,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import (
    RegistryActionsService,
    validate_action_template,
)
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.repository import Repository
from tracecat.registry.versions.schemas import RegistryVersionManifestAction
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.validation.schemas import ActionValidationResult, ValidationResultType
from tracecat.validation.service import validate_dsl

TEST_VERSION = "test-version"


async def create_manifest_for_actions(
    session: AsyncSession,
    repo_id: UUID,
    actions: list[BoundRegistryAction],
    organization_id: UUID | None,
) -> RegistryLock:
    """Create a RegistryVersion with manifest for the given actions."""
    assert organization_id is not None, "organization_id must be provided"

    result = await session.execute(
        select(RegistryRepository).where(RegistryRepository.id == repo_id)
    )
    repo = result.scalar_one()
    origin = repo.origin

    manifest_actions = {}
    action_bindings = {}

    for bound_action in actions:
        action_create = RegistryActionCreate.from_bound(bound_action, repo_id)
        action_name = f"{action_create.namespace}.{action_create.name}"
        manifest_action = RegistryVersionManifestAction.from_action_create(
            action_create
        )
        manifest_actions[action_name] = manifest_action.model_dump(mode="json")
        action_bindings[action_name] = origin

    # Add core.transform.reshape
    core_reshape_impl = {
        "type": "udf",
        "url": origin,
        "module": "tracecat_registry.core.transform",
        "name": "reshape",
    }
    manifest_actions["core.transform.reshape"] = {
        "namespace": "core.transform",
        "name": "reshape",
        "action_type": "udf",
        "description": "Transform data",
        "interface": {"expects": {}, "returns": None},
        "implementation": core_reshape_impl,
    }
    action_bindings["core.transform.reshape"] = origin

    manifest = {"schema_version": "1.0", "actions": manifest_actions}

    rv = RegistryVersion(
        organization_id=organization_id,
        repository_id=repo_id,
        version=TEST_VERSION,
        manifest=manifest,
        tarball_uri="s3://test/test.tar.gz",
    )

    session.add(rv)
    await session.flush()

    # Update the repository's current_version_id to point to this version
    repo.current_version_id = rv.id
    await session.commit()

    # Refresh after commit to ensure manifest data is loaded from DB
    await session.refresh(rv)

    versions_svc = RegistryVersionsService(session)
    await versions_svc.populate_index_from_manifest(rv, commit=True)

    return RegistryLock(
        origins={origin: TEST_VERSION},
        actions=action_bindings,
    )


async def run_action_test(input: RunActionInput, role) -> Any:
    """Test helper: execute action using production code path."""
    from tracecat.contexts import ctx_role

    ctx_role.set(role)
    backend = DirectBackend()
    return await dispatch_action(backend, input)


@pytest.fixture
def mock_run_context():
    wf_id = "wf-" + "0" * 32
    exec_id = "exec-" + "0" * 32
    wf_exec_id = f"{wf_id}:{exec_id}"
    run_id = uuid.uuid4()
    return RunContext(
        wf_id=WorkflowUUID.from_legacy(wf_id),
        wf_exec_id=wf_exec_id,
        wf_run_id=run_id,
        environment="default",
        logical_time=datetime.now(UTC),
    )


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


def test_validate_args_type_preservation_modes():
    """Test that validate_args preserves Python types in 'python' mode and serializes in 'json' mode."""

    repo = Repository()

    # Register UDF with datetime parameter
    @registry.register(
        description="Test function with datetime",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f_datetime(
        dt: Annotated[
            datetime,
            Doc("A datetime field"),
        ],
    ) -> datetime:
        return dt

    repo._register_udf_from_function(f_datetime, name="f_datetime")
    udf_datetime = repo.get("test.f_datetime")

    # Register UDF with UUID parameter
    @registry.register(
        description="Test function with UUID",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f_uuid(
        uid: Annotated[
            UUID,
            Doc("A UUID field"),
        ],
    ) -> UUID:
        return uid

    repo._register_udf_from_function(f_uuid, name="f_uuid")
    udf_uuid = repo.get("test.f_uuid")

    # Register UDF with set parameter
    @registry.register(
        description="Test function with set",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f_set(
        data: Annotated[
            set[int],
            Doc("A set of integers"),
        ],
    ) -> set[int]:
        return data

    repo._register_udf_from_function(f_set, name="f_set")
    udf_set = repo.get("test.f_set")

    # Register UDF with tuple parameter
    @registry.register(
        description="Test function with tuple",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f_tuple(
        tup: Annotated[
            tuple[str, int],
            Doc("A tuple of string and int"),
        ],
    ) -> tuple[str, int]:
        return tup

    repo._register_udf_from_function(f_tuple, name="f_tuple")
    udf_tuple = repo.get("test.f_tuple")

    # Register UDF with nested complex types
    @registry.register(
        description="Test function with nested complex types",
        namespace="test",
        doc_url="https://example.com/docs",
        author="Tracecat",
    )
    def f_nested(
        date_dict: Annotated[
            dict[str, datetime],
            Doc("A dict with datetime values"),
        ],
        uuid_list: Annotated[
            list[UUID],
            Doc("A list of UUIDs"),
        ],
    ) -> dict[str, Any]:
        return {"date_dict": date_dict, "uuid_list": uuid_list}

    repo._register_udf_from_function(f_nested, name="f_nested")
    udf_nested = repo.get("test.f_nested")

    # Test data
    test_datetime = datetime(2024, 1, 1, 12, 0, 0)
    test_uuid = UUID("12345678-1234-5678-1234-567812345678")
    test_set = {1, 2, 3}
    test_tuple = ("test", 42)
    test_date_dict = {"created": datetime(2024, 1, 1), "updated": datetime(2024, 1, 2)}
    test_uuid_list = [
        UUID("12345678-1234-5678-1234-567812345678"),
        UUID("87654321-4321-8765-4321-876543210987"),
    ]

    # TEST 1: mode="python" preserves native Python types
    result_dt_python = udf_datetime.validate_args(
        args={"dt": test_datetime}, mode="python"
    )
    assert isinstance(result_dt_python["dt"], datetime), (
        f"Expected datetime, got {type(result_dt_python['dt'])}"
    )
    assert result_dt_python["dt"] == test_datetime

    result_uuid_python = udf_uuid.validate_args(args={"uid": test_uuid}, mode="python")
    assert isinstance(result_uuid_python["uid"], UUID), (
        f"Expected UUID, got {type(result_uuid_python['uid'])}"
    )
    assert result_uuid_python["uid"] == test_uuid

    result_set_python = udf_set.validate_args(args={"data": test_set}, mode="python")
    assert isinstance(result_set_python["data"], set), (
        f"Expected set, got {type(result_set_python['data'])}"
    )
    assert result_set_python["data"] == test_set

    result_tuple_python = udf_tuple.validate_args(
        args={"tup": test_tuple}, mode="python"
    )
    assert isinstance(result_tuple_python["tup"], tuple), (
        f"Expected tuple, got {type(result_tuple_python['tup'])}"
    )
    assert result_tuple_python["tup"] == test_tuple

    result_nested_python = udf_nested.validate_args(
        args={"date_dict": test_date_dict, "uuid_list": test_uuid_list}, mode="python"
    )
    assert isinstance(result_nested_python["date_dict"], dict)
    assert isinstance(result_nested_python["date_dict"]["created"], datetime)
    assert isinstance(result_nested_python["uuid_list"], list)
    assert isinstance(result_nested_python["uuid_list"][0], UUID)

    # TEST 2: mode="json" serializes to JSON-compatible types
    result_dt_json = udf_datetime.validate_args(args={"dt": test_datetime}, mode="json")
    assert isinstance(result_dt_json["dt"], str), (
        f"Expected str, got {type(result_dt_json['dt'])}"
    )
    assert result_dt_json["dt"] == "2024-01-01T12:00:00"

    result_uuid_json = udf_uuid.validate_args(args={"uid": test_uuid}, mode="json")
    assert isinstance(result_uuid_json["uid"], str), (
        f"Expected str, got {type(result_uuid_json['uid'])}"
    )
    assert result_uuid_json["uid"] == "12345678-1234-5678-1234-567812345678"

    result_set_json = udf_set.validate_args(args={"data": test_set}, mode="json")
    assert isinstance(result_set_json["data"], list), (
        f"Expected list, got {type(result_set_json['data'])}"
    )
    assert set(result_set_json["data"]) == test_set  # Compare as sets

    result_tuple_json = udf_tuple.validate_args(args={"tup": test_tuple}, mode="json")
    assert isinstance(result_tuple_json["tup"], list), (
        f"Expected list, got {type(result_tuple_json['tup'])}"
    )
    assert result_tuple_json["tup"] == ["test", 42]

    result_nested_json = udf_nested.validate_args(
        args={"date_dict": test_date_dict, "uuid_list": test_uuid_list}, mode="json"
    )
    assert isinstance(result_nested_json["date_dict"], dict)
    assert isinstance(
        result_nested_json["date_dict"]["created"], str
    )  # datetime serialized
    assert isinstance(result_nested_json["uuid_list"], list)
    assert isinstance(result_nested_json["uuid_list"][0], str)  # UUID serialized

    # TEST 3: Template expressions work in both modes
    udf_datetime.validate_args(args={"dt": "${{ INPUTS.date }}"}, mode="python")
    udf_datetime.validate_args(args={"dt": "${{ INPUTS.date }}"}, mode="json")

    udf_uuid.validate_args(args={"uid": "${{ INPUTS.id }}"}, mode="python")
    udf_uuid.validate_args(args={"uid": "${{ INPUTS.id }}"}, mode="json")

    # Should not raise any validation errors


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_with_optional_oauth_both_ac_and_cc(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that Template Action correctly handles optional AC and CC OAuth secrets.

    This test validates that:
    1. Actions work when both optional credentials are present
    2. Actions work when neither optional credential is present (graceful degradation)
    3. Required credentials still raise errors when missing
    """
    session, db_repo_id = db_session_with_repo

    # Disable secrets masking for this test
    monkeysession.setattr("tracecat.config.TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    # Test OAuth token values
    ac_token_value = "__TEST_AC_TOKEN__"
    cc_token_value = "__TEST_CC_TOKEN__"

    # Create a test template action with both AC and CC OAuth secrets as optional
    test_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Optional OAuth",
            description="Test optional AC and CC OAuth credentials",
            name="optional_oauth_test",
            namespace="testing.oauth",
            display_group="Testing",
            expects={
                "message": ExpectedField(
                    type="str",
                    description="A test message",
                )
            },
            secrets=[
                RegistryOAuthSecret(
                    provider_id="microsoft_teams",
                    grant_type="authorization_code",
                    optional=True,
                ),
                RegistryOAuthSecret(
                    provider_id="microsoft_teams",
                    grant_type="client_credentials",
                    optional=True,
                ),
            ],
            steps=[
                ActionStep(
                    ref="get_tokens",
                    action="core.transform.reshape",
                    args={
                        "value": {
                            "ac_token": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_USER_TOKEN || 'NOT_SET' }}",
                            "cc_token": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_SERVICE_TOKEN || 'NOT_SET' }}",
                            "message": "${{ inputs.message }}",
                        }
                    },
                )
            ],
            returns="${{ steps.get_tokens.result }}",
        ),
    )

    # Create a second template action with required OAuth credential
    test_action_required = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Required OAuth",
            description="Test required AC OAuth credential",
            name="required_oauth_test",
            namespace="testing.oauth",
            display_group="Testing",
            expects={
                "message": ExpectedField(
                    type="str",
                    description="A test message",
                )
            },
            secrets=[
                RegistryOAuthSecret(
                    provider_id="microsoft_teams",
                    grant_type="authorization_code",
                    optional=False,  # Required
                ),
            ],
            steps=[
                ActionStep(
                    ref="get_token",
                    action="core.transform.reshape",
                    args={
                        "value": {
                            "ac_token": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_USER_TOKEN }}",
                        }
                    },
                )
            ],
            returns="${{ steps.get_token.result }}",
        ),
    )

    # Register both template actions
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(test_action)
    repo.register_template_action(test_action_required)

    # Validate both template actions
    bound_action = repo.get("testing.oauth.optional_oauth_test")
    validation_errors = await validate_action_template(bound_action, repo)
    assert len(validation_errors) == 0, (
        f"Template validation failed: {validation_errors}"
    )

    bound_action_required = repo.get("testing.oauth.required_oauth_test")
    validation_errors_required = await validate_action_template(
        bound_action_required, repo, check_db=False
    )
    assert len(validation_errors_required) == 0, (
        f"Template validation failed: {validation_errors_required}"
    )

    # Create both actions in the database
    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(bound_action, db_repo_id)
    )
    await ra_service.create_action(
        RegistryActionCreate.from_bound(bound_action_required, db_repo_id)
    )

    # Create manifest for both test actions
    registry_lock = await create_manifest_for_actions(
        session,
        db_repo_id,
        [bound_action, bound_action_required],
        test_role.organization_id,
    )

    # Helper function to run the optional action
    async def run_test_action():
        input = RunActionInput(
            task=ActionStatement(
                ref="test",
                action="testing.oauth.optional_oauth_test",
                run_if=None,
                for_each=None,
                args={"message": "test message"},
            ),
            exec_context=create_default_execution_context(),
            run_context=mock_run_context,
            registry_lock=registry_lock,
        )
        return await run_action_test(input, test_role)

    # Test 1: Both credentials present - should work
    svc = IntegrationService(session, role=test_role)
    await svc.store_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        ),
        access_token=SecretStr(ac_token_value),
        refresh_token=None,
        expires_in=3600,
    )
    await svc.store_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
        ),
        access_token=SecretStr(cc_token_value),
        refresh_token=None,
        expires_in=3600,
    )

    result = await run_test_action()
    assert isinstance(result, dict)
    assert result["ac_token"] == ac_token_value
    assert result["cc_token"] == cc_token_value
    assert result["message"] == "test message"

    # Test 2: Neither credential present (both optional) - should still work
    ac_integration = await svc.get_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
    )
    if ac_integration:
        await svc.remove_integration(integration=ac_integration)

    cc_integration = await svc.get_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
        )
    )
    if cc_integration:
        await svc.remove_integration(integration=cc_integration)

    result = await run_test_action()
    assert isinstance(result, dict)
    assert result["ac_token"] == "NOT_SET"
    assert result["cc_token"] == "NOT_SET"
    assert result["message"] == "test message"

    # Test 3: Required credential missing should fail
    input_required = RunActionInput(
        task=ActionStatement(
            ref="test_required",
            action="testing.oauth.required_oauth_test",
            run_if=None,
            for_each=None,
            args={"message": "test message"},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    # Should raise error when required credential is missing
    with pytest.raises(ExecutionError) as exc_info:
        await run_action_test(input_required, test_role)

    assert "Missing required OAuth integrations" in str(exc_info.value)
    assert exc_info.value.info.type == "TracecatCredentialsError"


@pytest.mark.integration
@pytest.mark.anyio
async def test_validate_dsl_with_optional_oauth_credentials(
    test_role, db_session_with_repo
):
    """Test that validate_dsl() correctly handles optional OAuth credentials.

    This test reproduces the bug where validate_dsl() treats all OAuth credentials
    as required, even when marked as optional=True.
    """

    session, db_repo_id = db_session_with_repo

    # Create a template action with optional OAuth credentials (both AC and CC)
    test_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Optional OAuth DSL",
            description="Test DSL validation with optional OAuth",
            name="optional_oauth_dsl_test",
            namespace="testing.oauth",
            display_group="Testing",
            expects={
                "message": ExpectedField(
                    type="str",
                    description="A test message",
                )
            },
            secrets=[
                RegistryOAuthSecret(
                    provider_id="microsoft_teams",
                    grant_type="authorization_code",
                    optional=True,  # Optional!
                ),
                RegistryOAuthSecret(
                    provider_id="microsoft_teams",
                    grant_type="client_credentials",
                    optional=True,  # Optional!
                ),
            ],
            steps=[
                ActionStep(
                    ref="get_tokens",
                    action="core.transform.reshape",
                    args={
                        "value": {
                            "ac_token": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_USER_TOKEN || 'NOT_SET' }}",
                            "cc_token": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_SERVICE_TOKEN || 'NOT_SET' }}",
                            "message": "${{ inputs.message }}",
                        }
                    },
                )
            ],
            returns="${{ steps.get_tokens.result }}",
        ),
    )

    # Register the template action
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(test_action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(
            repo.get("testing.oauth.optional_oauth_dsl_test"), db_repo_id
        )
    )

    # Create a DSL that uses this action
    dsl = DSLInput(
        title="Test Workflow",
        description="Test workflow with optional OAuth",
        entrypoint=DSLEntrypoint(expects={}),
        actions=[
            ActionStatement(
                ref="test_action",
                action="testing.oauth.optional_oauth_dsl_test",
                args={"message": "test"},
            )
        ],
    )

    # Validate the DSL - this should NOT fail for optional OAuth credentials
    # BUG: Currently this will fail because validate_workspace_integration doesn't check optional field
    validation_results = await validate_dsl(session, dsl, role=test_role)

    # Filter for secret validation errors
    secret_errors = [
        r for r in validation_results if r.root.type == ValidationResultType.SECRET
    ]

    # This assertion will FAIL with the current bug, demonstrating the issue
    # Optional OAuth credentials should not cause validation errors
    assert len(secret_errors) == 0, (
        f"Expected no secret validation errors for optional OAuth credentials, "
        f"but got {len(secret_errors)}: {secret_errors}"
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_agent_tool_approvals_requires_feature_flag(
    test_role, db_session_with_repo, monkeypatch
):
    session, db_repo_id = db_session_with_repo

    # Ensure feature flag disabled
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())

    repo = Repository()
    repo.init(include_base=True, include_templates=False)

    ra_service = RegistryActionsService(session, role=test_role)
    bound_action = repo.get("ai.agent")
    action_create = RegistryActionCreate.from_bound(bound_action, db_repo_id)

    # Ensure the agent action is registered exactly once.
    # It may already exist if the base registry has been synced.
    if await ra_service.get_action_or_none("ai.agent") is None:
        await ra_service.create_action(action_create)

    # Create manifest for the ai.agent action using the helper
    await create_manifest_for_actions(
        session, db_repo_id, [bound_action], test_role.organization_id
    )

    dsl = DSLInput(
        title="Test Workflow",
        description="Agent with tool approvals",
        entrypoint=DSLEntrypoint(expects={}),
        actions=[
            ActionStatement(
                ref="agent_action",
                action="ai.agent",
                args={
                    "user_prompt": "Hello",
                    "model_name": "gpt-4o-mini",
                    "model_provider": "openai",
                    "actions": ["tools.slack.post_message"],
                    "tool_approvals": {"tools.slack.post_message": True},
                },
            )
        ],
    )

    validation_results = await validate_dsl(session, dsl, role=test_role)
    action_errors = [
        r for r in validation_results if r.root.type == ValidationResultType.ACTION
    ]

    assert len(action_errors) == 1
    root = action_errors[0].root
    assert isinstance(root, ActionValidationResult)
    detail = root.detail
    if detail is None:
        detail_msgs: set[str] = set()
    else:
        detail_msgs = {d.msg for d in detail}
    assert any("agent-approvals" in msg for msg in detail_msgs)


@pytest.mark.integration
@pytest.mark.anyio
async def test_agent_tool_approvals_passes_with_feature_flag(
    test_role, db_session_with_repo, monkeypatch
):
    session, db_repo_id = db_session_with_repo

    monkeypatch.setattr(
        config,
        "TRACECAT__FEATURE_FLAGS",
        {FeatureFlag.AGENT_APPROVALS},
    )

    repo = Repository()
    repo.init(include_base=True, include_templates=False)

    ra_service = RegistryActionsService(session, role=test_role)
    bound_action = repo.get("ai.agent")
    action_create = RegistryActionCreate.from_bound(bound_action, db_repo_id)

    # Ensure the agent action is registered exactly once.
    # It may already exist if the base registry has been synced.
    if await ra_service.get_action_or_none("ai.agent") is None:
        await ra_service.create_action(action_create)

    # Create manifest for the ai.agent action using the helper
    await create_manifest_for_actions(
        session, db_repo_id, [bound_action], test_role.organization_id
    )

    dsl = DSLInput(
        title="Test Workflow",
        description="Agent with tool approvals",
        entrypoint=DSLEntrypoint(expects={}),
        actions=[
            ActionStatement(
                ref="agent_action",
                action="ai.agent",
                args={
                    "user_prompt": "Hello",
                    "model_name": "gpt-4o-mini",
                    "model_provider": "openai",
                    "actions": ["tools.slack.post_message"],
                    "tool_approvals": {"tools.slack.post_message": True},
                },
            )
        ],
    )

    validation_results = await validate_dsl(session, dsl, role=test_role)
    action_errors = [
        r for r in validation_results if r.root.type == ValidationResultType.ACTION
    ]

    assert len(action_errors) == 0
