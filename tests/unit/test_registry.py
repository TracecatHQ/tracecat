import os
import textwrap

import pytest
from tracecat_registry import RegistrySecret

from tracecat.executor import service
from tracecat.expressions.expectations import ExpectedField
from tracecat.git import GitUrl, parse_git_url
from tracecat.registry.actions.models import (
    ActionStep,
    TemplateAction,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.repository import Repository
from tracecat.types.exceptions import RegistryValidationError


@pytest.mark.anyio
async def test_list_registry_actions(test_role):
    """Test that the list_registry_actions endpoint returns the correct number of actions."""
    async with RegistryActionsService.with_session(test_role) as service:
        actions = await service.list_actions()
        results = []
        # Call serially instead of calling in parallel to avoid hiding exceptions
        for action in actions:
            results.append(await service.read_action_with_implicit_secrets(action))
        assert len(results) == len(actions)


@pytest.fixture
def mock_package(tmp_path):
    """Pytest fixture that creates a mock package with files and cleans up after the test."""
    import sys
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    # Create a new module
    test_module = ModuleType("test_module")

    # Create a module spec for the test module
    module_spec = ModuleSpec("test_module", None)
    test_module.__spec__ = module_spec
    # Set __path__ to the temporary directory
    test_module.__path__ = [str(tmp_path)]

    try:
        # Add the module to sys.modules
        sys.modules["test_module"] = test_module
        # Create a file for the sync function
        with open(os.path.join(tmp_path, "sync_function.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry

                @registry.register(
                    description="This is a test function",
                    namespace="test",
                    doc_url="https://example.com/docs",
                    author="Tracecat",
                )
                def test_function(num: int) -> int:
                    return num
            """
                )
            )

        # Create a file for the async function
        with open(os.path.join(tmp_path, "async_function.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry

                @registry.register(
                    description="This is an async test function",
                    namespace="test",
                    doc_url="https://example.com/docs",
                    author="Tracecat",
                )
                async def async_test_function(num: int) -> int:
                    return num
            """
                )
            )

        # Create a file for the deprecated function
        with open(os.path.join(tmp_path, "deprecated_function.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry

                @registry.register(
                    description="This is a deprecated function",
                    namespace="test",
                    deprecated="This function is deprecated",
                )
                def deprecated_function(num: int) -> int:
                    return num
            """
                )
            )

        yield test_module

    finally:
        # Clean up
        del sys.modules["test_module"]


def test_udf_can_be_registered(mock_package):
    """Test that a UDF can be registered."""
    repo = Repository()
    repo._register_udfs_from_package(mock_package)
    assert repo.get("test.test_function") is not None


def test_udf_validate_args(mock_package):
    """This tests the UDF.validate_args method, which shouldn't raise any exceptions
    when given a templated expression.
    """
    # Register UDFs from the mock package
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Get the registered UDF
    udf = repo.get("test.test_function")

    # Check descriptors
    assert udf.description == "This is a test function"
    assert udf.namespace == "test"
    assert udf.doc_url == "https://example.com/docs"
    assert udf.author == "Tracecat"

    # Test the UDF
    udf.validate_args(num="${{ path.to.number }}")
    udf.validate_args(num=1)
    with pytest.raises(RegistryValidationError):
        udf.validate_args(num="not a number")


def test_deprecated_function_can_be_registered(mock_package):
    """Test that a deprecated function can be registered."""
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    udf = repo.get("test.deprecated_function")
    assert udf is not None

    # Check descriptors
    assert udf.deprecated == "This function is deprecated"


def test_registry_function_can_be_called(mock_package):
    """We need to test that the ordering of the workflow tasks is correct."""
    repo = Repository()
    assert len(repo) == 0

    repo._register_udfs_from_package(mock_package)
    udf = repo.get("test.test_function")
    for i in range(10):
        assert udf.fn(num=i) == i


@pytest.mark.anyio
async def test_registry_async_function_can_be_called(mock_package):
    repo = Repository()
    assert len(repo) == 0

    repo._register_udfs_from_package(mock_package)
    udf = repo.get("test.async_test_function")
    for i in range(10):
        assert await udf.fn(num=i) == i


@pytest.mark.parametrize(
    "url, expected",
    [
        # GitHub (no branch)
        (
            "git+ssh://git@github.com/org/repo",
            GitUrl(
                host="github.com",
                org="org",
                repo="repo",
                ref=None,
            ),
        ),
        # GitHub (with branch/sha)
        (
            "git+ssh://git@github.com/org/repo@branchOrSHAOrTag",
            GitUrl(
                host="github.com",
                org="org",
                repo="repo",
                ref="branchOrSHAOrTag",
            ),
        ),
        # GitLab
        (
            "git+ssh://git@gitlab.com/org/repo",
            GitUrl(
                host="gitlab.com",
                org="org",
                repo="repo",
                ref=None,
            ),
        ),
        # GitLab (with branch)
        (
            "git+ssh://git@gitlab.com/org/repo@branch",
            GitUrl(
                host="gitlab.com",
                org="org",
                repo="repo",
                ref="branch",
            ),
        ),
        # Private GitLab
        (
            "git+ssh://git@internal.tracecat/org/repo",
            GitUrl(
                host="internal.tracecat",
                org="org",
                repo="repo",
                ref=None,
            ),
        ),
        # # Private GitLab nested in a subdirectory
        # (
        #     "git+ssh://git@internal.tracecat/org/group/repo",
        #     GitUrl(
        #         host="internal.tracecat",
        #         org="org/group",
        #         repo="repo",
        #         ref=None,
        #     ),
        # ),
    ],
)
def test_parse_git_url(url: str, expected: GitUrl):
    assert parse_git_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(
            "git+ssh://git@tracecat.com/tracecat-dev/tracecat-registry.git@v1.0.0",
            id="Invalid host domain tracecat.com",
        ),
        pytest.param(
            "git+ssh://git@git.com/tracecat-dev/tracecat-registry.git@v1.0.0",
            id="Invalid host domain git.com",
        ),
        pytest.param(
            "https://github.com/org/repo",
            id="Invalid URL scheme - must be git+ssh",
        ),
        pytest.param(
            "git+ssh://git@github.com/org",
            id="Missing repository name",
        ),
        pytest.param(
            "git+ssh://git@github.com/org/repo@branch/extra",
            id="Invalid branch format with extra path component",
        ),
    ],
)
def test_parse_git_url_invalid(url: str):
    allowed_domains = {"github.com", "gitlab.com"}
    with pytest.raises(ValueError):
        parse_git_url(url, allowed_domains=allowed_domains)


def test_construct_template_action():
    data = {
        "type": "action",
        "definition": {
            "title": "Test Action",
            "description": "This is just a test",
            "name": "wrapper",
            "namespace": "integrations.test",
            "display_group": "Testing",
            "secrets": [{"name": "test_secret", "keys": ["KEY"]}],
            "expects": {
                "service_source": {
                    "type": "str",
                    "description": "The service source",
                    "default": "elastic",
                },
                "limit": {"type": "int | None", "description": "The limit"},
            },
            "steps": [
                {
                    "ref": "base",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "service_source": "${{ inputs.service_source }}",
                            "data": 100,
                        }
                    },
                },
                {
                    "ref": "final",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": [
                            "${{ steps.base.result.data + 100 }}",
                            "${{ steps.base.result.service_source }}",
                        ]
                    },
                },
            ],
            "returns": "${{ steps.final.result }}",
        },
    }

    # Parse and validate the action
    action = TemplateAction.model_validate(data)

    # Check the action definition
    assert action.definition.title == "Test Action"
    assert action.definition.description == "This is just a test"
    assert action.definition.action == "integrations.test.wrapper"
    assert action.definition.namespace == "integrations.test"
    assert action.definition.display_group == "Testing"
    assert action.definition.secrets == [
        RegistrySecret(name="test_secret", keys=["KEY"])
    ]
    assert action.definition.expects == {
        "service_source": ExpectedField(
            type="str",
            description="The service source",
            default="elastic",
        ),
        "limit": ExpectedField(
            type="int | None",
            description="The limit",
        ),
    }
    assert action.definition.steps == [
        ActionStep(
            ref="base",
            action="core.transform.reshape",
            args={
                "value": {
                    "service_source": "${{ inputs.service_source }}",
                    "data": 100,
                }
            },
        ),
        ActionStep(
            ref="final",
            action="core.transform.reshape",
            args={
                "value": [
                    "${{ steps.base.result.data + 100 }}",
                    "${{ steps.base.result.service_source }}",
                ]
            },
        ),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_args,expected_result,should_raise",
    [
        (
            {"user_id": "john@tracecat.com", "service_source": "custom", "limit": 99},
            ["john@tracecat.com", "custom", 99],
            False,
        ),
        (
            {"user_id": "john@tracecat.com"},
            ["john@tracecat.com", "elastic", 100],
            False,
        ),
        (
            {},
            None,
            True,
        ),
    ],
    ids=["valid", "with_defaults", "missing_required"],
)
async def test_template_action_run(test_args, expected_result, should_raise):
    action = TemplateAction(
        **{
            "type": "action",
            "definition": {
                "title": "Test Action",
                "description": "This is just a test",
                "name": "wrapper",
                "namespace": "integrations.test",
                "display_group": "Testing",
                "doc_url": "https://example.com/docs",
                "author": "Tracecat",
                "secrets": [{"name": "test_secret", "keys": ["KEY"]}],
                "expects": {
                    # Required field
                    "user_id": {
                        "type": "str",
                        "description": "The user ID",
                    },
                    # Optional field with string defaultß
                    "service_source": {
                        "type": "str",
                        "description": "The service source",
                        "default": "elastic",
                    },
                    # Optional field with None as default
                    "limit": {
                        "type": "int | None",
                        "description": "The limit",
                        "default": None,
                    },
                },
                "steps": [
                    {
                        "ref": "base",
                        "action": "core.transform.reshape",
                        "args": {
                            "value": {
                                "service_source": "${{ inputs.service_source }}",
                                "data": "${{ inputs.limit || 100 }}",
                            }
                        },
                    },
                    {
                        "ref": "final",
                        "action": "core.transform.reshape",
                        "args": {
                            "value": [
                                "${{ inputs.user_id }}",
                                "${{ steps.base.result.service_source }}",
                                "${{ steps.base.result.data }}",
                            ]
                        },
                    },
                ],
                "returns": "${{ steps.final.result }}",
            },
        }
    )

    # Register the action
    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)

    # Check that the action is registered
    assert action.definition.action == "integrations.test.wrapper"
    assert "core.transform.reshape" in registry
    assert action.definition.action in registry

    # Get the registered action
    bound_action = registry.get(action.definition.action)

    # Run the action
    if should_raise:
        with pytest.raises(RegistryValidationError):
            await service.run_template_action(
                action=bound_action,
                args=test_args,
                context={},
            )
    else:
        result = await service.run_template_action(
            action=bound_action,
            args=test_args,
            context={},
        )
        assert result == expected_result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_args,expected_result,should_raise",
    [
        (
            {"status": "critical", "message": "System CPU usage above 90%"},
            {"alert_status": "critical", "alert_message": "System CPU usage above 90%"},
            False,
        ),
        (
            {"message": "Informational message"},
            {"alert_status": "info", "alert_message": "Informational message"},
            False,
        ),
        (
            {
                "status": "emergency",
                "message": "This should fail",
            },
            None,
            True,
        ),
    ],
    ids=["valid_status", "default_status", "invalid_status"],
)
async def test_enum_template_action(test_args, expected_result, should_raise):
    """Test template action with enum status.
    This test verifies that:
    1. The action can be constructed with an enum status
    2. The action can be run with a valid enum status
    3. The action can be run with a default enum status
    4. Invalid enum values are properly rejected
    """
    data = {
        "type": "action",
        "definition": {
            "title": "Test Alert Action",
            "description": "Test action with enum status",
            "name": "alert",
            "namespace": "integrations.test",
            "display_group": "Testing",
            "doc_url": "https://example.com/docs",
            "author": "Tracecat",
            "expects": {
                "status": {
                    "type": 'enum["critical", "warning", "info"]',
                    "description": "Alert severity level",
                    "default": "info",
                },
                "message": {"type": "str", "description": "Alert message"},
            },
            "steps": [
                {
                    "ref": "format",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "alert_status": "${{ inputs.status }}",
                            "alert_message": "${{ inputs.message }}",
                        }
                    },
                }
            ],
            "returns": "${{ steps.format.result }}",
        },
    }

    # Parse and validate the action
    action = TemplateAction.model_validate(data)

    # Register the action
    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)

    # Get the registered action
    bound_action = registry.get(action.definition.action)

    # Run the action
    if should_raise:
        with pytest.raises(RegistryValidationError):
            await service.run_template_action(
                action=bound_action,
                args=test_args,
                context={},
            )
    else:
        result = await service.run_template_action(
            action=bound_action,
            args=test_args,
            context={},
        )
        assert result == expected_result
