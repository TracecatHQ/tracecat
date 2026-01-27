"""Integration tests for template action execution via ExecutorBackend.

These tests verify that template actions work correctly through the service layer
orchestration, including with different backends (direct, sandboxed).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import RegistryVersion
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.service import dispatch_action
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.schemas import (
    ActionStep,
    RegistryActionCreate,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.repository import Repository
from tracecat.registry.versions.schemas import RegistryVersionManifestAction
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService

TEST_VERSION = "test-version"


async def create_manifest_for_actions(
    session: AsyncSession,
    repo_id: UUID,
    actions: list[BoundRegistryAction],
) -> RegistryLock:
    """Create a RegistryVersion with manifest for the given actions."""
    from sqlalchemy import select

    from tracecat.db.models import RegistryRepository

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
        organization_id=uuid.uuid4(),
        repository_id=repo_id,
        version=TEST_VERSION,
        manifest=manifest,
        tarball_uri="s3://test/test.tar.gz",
    )
    session.add(rv)
    await session.commit()

    versions_svc = RegistryVersionsService(session)
    await versions_svc.populate_index_from_manifest(rv, commit=True)

    return RegistryLock(
        origins={origin: TEST_VERSION},
        actions=action_bindings,
    )


def make_registry_lock(action: str, origin: str = "tracecat_registry") -> RegistryLock:
    """Helper to create a RegistryLock for a single action (for unit tests with mocks)."""
    return RegistryLock(
        origins={origin: TEST_VERSION},
        actions={action: origin},
    )


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


async def run_template_test(
    input: RunActionInput, role: Role, backend: DirectBackend | None = None
) -> Any:
    """Test helper: execute template action using production code path."""
    if backend is None:
        backend = DirectBackend()
    ctx_role.set(role)
    return await dispatch_action(backend, input)


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_two_steps_via_direct_backend(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that a 2-step template executes correctly via DirectBackend.

    This verifies the service-layer orchestration where each step becomes
    a separate backend.execute() call.
    """
    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    session, db_repo_id = db_session_with_repo

    # Create a 2-step template: step1 adds 100, step2 multiplies by 2
    # This tests that step results are passed correctly between steps
    action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Two Step Math",
            description="Performs two math operations",
            name="two_step_math",
            namespace="testing",
            display_group="Testing",
            expects={
                "initial_value": ExpectedField(
                    type="int",
                    description="Initial value to transform",
                )
            },
            secrets=[],
            steps=[
                ActionStep(
                    ref="add_step",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ inputs.initial_value + 100 }}",
                    },
                ),
                ActionStep(
                    ref="multiply_step",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ steps.add_step.result * 2 }}",
                    },
                ),
            ],
            returns="${{ steps.multiply_step.result }}",
        ),
    )

    # Register the template action
    repo = Repository()
    repo.register_template_action(action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.two_step_math"), db_repo_id)
    )

    # Create manifest for the test actions
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [repo.get("testing.two_step_math")]
    )

    # Create and run the action
    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.two_step_math",
            args={"initial_value": 50},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    # Act
    result = await run_template_test(input, test_role)

    # Assert: (50 + 100) * 2 = 300
    assert result == 300


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_step_results_accessible(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that step results are accessible to subsequent steps via steps context."""
    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    session, db_repo_id = db_session_with_repo

    # Create a template that passes data between steps using steps context
    action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Step Data Flow",
            description="Tests step data flow",
            name="step_data_flow",
            namespace="testing",
            display_group="Testing",
            expects={
                "name": ExpectedField(
                    type="str",
                    description="Name to process",
                )
            },
            secrets=[],
            steps=[
                ActionStep(
                    ref="create_greeting",
                    action="core.transform.reshape",
                    args={
                        "value": {"greeting": "Hello, ${{ inputs.name }}!"},
                    },
                ),
                ActionStep(
                    ref="add_timestamp",
                    action="core.transform.reshape",
                    args={
                        "value": {
                            "original": "${{ steps.create_greeting.result }}",
                            "with_time": "${{ steps.create_greeting.result.greeting }} - processed",
                        },
                    },
                ),
            ],
            returns="${{ steps.add_timestamp.result }}",
        ),
    )

    repo = Repository()
    repo.register_template_action(action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.step_data_flow"), db_repo_id)
    )

    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [repo.get("testing.step_data_flow")]
    )

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.step_data_flow",
            args={"name": "World"},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    result = await run_template_test(input, test_role)

    assert result["original"] == {"greeting": "Hello, World!"}
    assert result["with_time"] == "Hello, World! - processed"


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_returns_expression_evaluated(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that the returns expression is evaluated correctly with final context."""
    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    session, db_repo_id = db_session_with_repo

    # Create a template with a complex returns expression
    action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Complex Returns",
            description="Tests complex returns expressions",
            name="complex_returns",
            namespace="testing",
            display_group="Testing",
            expects={
                "a": ExpectedField(type="int", description="First number"),
                "b": ExpectedField(type="int", description="Second number"),
            },
            secrets=[],
            steps=[
                ActionStep(
                    ref="sum_step",
                    action="core.transform.reshape",
                    args={"value": "${{ inputs.a + inputs.b }}"},
                ),
                ActionStep(
                    ref="product_step",
                    action="core.transform.reshape",
                    args={"value": "${{ inputs.a * inputs.b }}"},
                ),
            ],
            # Returns a dict built from multiple step results
            returns={
                "sum": "${{ steps.sum_step.result }}",
                "product": "${{ steps.product_step.result }}",
                "inputs": {"a": "${{ inputs.a }}", "b": "${{ inputs.b }}"},
            },
        ),
    )

    repo = Repository()
    repo.register_template_action(action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.complex_returns"), db_repo_id)
    )

    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [repo.get("testing.complex_returns")]
    )

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.complex_returns",
            args={"a": 5, "b": 3},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    result = await run_template_test(input, test_role)

    assert result == {
        "sum": 8,
        "product": 15,
        "inputs": {"a": 5, "b": 3},
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_with_secrets_in_top_level_args(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that template actions can access secrets passed through top-level args.

    Note: Secrets referenced directly in step args (e.g., SECRETS.x.Y in step args)
    require the secret expression to be passed through the template's input args
    so they can be collected during prepare_resolved_context.
    """
    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    session, db_repo_id = db_session_with_repo

    # Create a secret
    sec_service = SecretsService(session, role=test_role)
    try:
        await sec_service.create_secret(
            SecretCreate(
                name="test_api",
                environment="default",
                keys=[
                    SecretKeyValue(
                        key="API_KEY",
                        value=SecretStr("super-secret-key"),
                    )
                ],
            )
        )

        # Create template that receives the secret via its expects
        action = TemplateAction(
            type="action",
            definition=TemplateActionDefinition(
                title="Secret User",
                description="Uses a secret passed as input",
                name="secret_user",
                namespace="testing",
                display_group="Testing",
                expects={
                    "api_key": ExpectedField(
                        type="str",
                        description="API key to use",
                    )
                },
                secrets=[],
                steps=[
                    ActionStep(
                        ref="use_secret",
                        action="core.transform.reshape",
                        args={
                            "value": {
                                "key": "${{ inputs.api_key }}",
                            }
                        },
                    ),
                ],
                returns="${{ steps.use_secret.result }}",
            ),
        )

        repo = Repository()
        repo.register_template_action(action)

        ra_service = RegistryActionsService(session, role=test_role)
        await ra_service.create_action(
            RegistryActionCreate.from_bound(repo.get("testing.secret_user"), db_repo_id)
        )

        registry_lock = await create_manifest_for_actions(
            session, db_repo_id, [repo.get("testing.secret_user")]
        )

        # Pass the secret expression in the top-level args
        # This allows collect_expressions to find and resolve it
        input = RunActionInput(
            task=ActionStatement(
                ref="test",
                action="testing.secret_user",
                args={"api_key": "${{ SECRETS.test_api.API_KEY }}"},
            ),
            exec_context=create_default_execution_context(),
            run_context=mock_run_context,
            registry_lock=registry_lock,
        )

        result = await run_template_test(input, test_role)

        assert result["key"] == "super-secret-key"
    finally:
        secret = await sec_service.get_secret_by_name("test_api")
        await sec_service.delete_secret(secret)


@pytest.mark.integration
@pytest.mark.anyio
async def test_nested_template_actions(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that nested templates (template calling template) work correctly."""
    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    session, db_repo_id = db_session_with_repo

    # Create inner template
    inner_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Inner Template",
            description="Inner template that doubles a value",
            name="inner_double",
            namespace="testing",
            display_group="Testing",
            expects={
                "value": ExpectedField(type="int", description="Value to double"),
            },
            secrets=[],
            steps=[
                ActionStep(
                    ref="double",
                    action="core.transform.reshape",
                    args={"value": "${{ inputs.value * 2 }}"},
                ),
            ],
            returns="${{ steps.double.result }}",
        ),
    )

    # Create outer template that calls inner template
    outer_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Outer Template",
            description="Outer template that calls inner template twice",
            name="outer_quad",
            namespace="testing",
            display_group="Testing",
            expects={
                "value": ExpectedField(type="int", description="Value to quadruple"),
            },
            secrets=[],
            steps=[
                ActionStep(
                    ref="first_double",
                    action="testing.inner_double",
                    args={"value": "${{ inputs.value }}"},
                ),
                ActionStep(
                    ref="second_double",
                    action="testing.inner_double",
                    args={"value": "${{ steps.first_double.result }}"},
                ),
            ],
            returns="${{ steps.second_double.result }}",
        ),
    )

    repo = Repository()
    repo.register_template_action(inner_action)
    repo.register_template_action(outer_action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.inner_double"), db_repo_id)
    )
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.outer_quad"), db_repo_id)
    )

    registry_lock = await create_manifest_for_actions(
        session,
        db_repo_id,
        [repo.get("testing.inner_double"), repo.get("testing.outer_quad")],
    )

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.outer_quad",
            args={"value": 5},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    result = await run_template_test(input, test_role)

    # 5 * 2 * 2 = 20
    assert result == 20


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_for_each(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that template actions work with for_each loops."""
    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    session, db_repo_id = db_session_with_repo

    # Create a simple template
    action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Add Ten",
            description="Adds 10 to input",
            name="add_ten",
            namespace="testing",
            display_group="Testing",
            expects={
                "value": ExpectedField(type="int", description="Value"),
            },
            secrets=[],
            steps=[
                ActionStep(
                    ref="add",
                    action="core.transform.reshape",
                    args={"value": "${{ inputs.value + 10 }}"},
                ),
            ],
            returns="${{ steps.add.result }}",
        ),
    )

    repo = Repository()
    repo.register_template_action(action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.add_ten"), db_repo_id)
    )

    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [repo.get("testing.add_ten")]
    )

    # Create input with for_each
    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.add_ten",
            for_each="${{ for var.item in [1, 2, 3] }}",
            args={"value": "${{ var.item }}"},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    result = await run_template_test(input, test_role)

    # Each value has 10 added: [11, 12, 13]
    assert result == [11, 12, 13]
