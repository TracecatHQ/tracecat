"""Classification must survive sanitization without carrying secret plaintext."""

from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from temporalio.api.failure.v1 import Failure
from temporalio.converter import DataConverter

from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import EntitlementRequired, ExecutionError, LoopExecutionError
from tracecat.executor import service
from tracecat.executor.error_policy import classify_execute_action_error
from tracecat.executor.registry_artifacts import (
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLeaseContentionError,
    RegistryArtifactExtractionError,
)
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResultFailure,
    ResolvedContext,
)
from tracecat.expressions.policy import build_provenance
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.sandbox.exceptions import SandboxInfrastructureError, SandboxWorkloadError
from tracecat.sandbox.types import SandboxErrorCode
from tracecat.temporal.errors import application_error_from_classification

CANARY = "synthetic-secret\nclassification-canary"
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("prepared")]


@pytest.fixture(params=[0, 2], ids=["direct", "nested"])
def template_depth(request: pytest.FixtureRequest) -> int:
    return request.param


@pytest.fixture(params=[False, True], ids=["plain", "secret"])
def secret_backed(request: pytest.FixtureRequest) -> bool:
    return request.param


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        organization_id=UUID(int=1),
        workspace_id=UUID(int=2),
        service_id="tracecat-executor",
    )


@pytest.fixture
def action_input(secret_backed: bool) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            ref="a",
            action="testing.level0",
            args={"value": "${{ SECRETS.svc.TOKEN }}" if secret_backed else "plain"},
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=generate_exec_id(wf_id),
            wf_run_id=UUID(int=3, version=4),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(origins={}, actions={}),
    )


@pytest.fixture
def prepared(
    monkeypatch: pytest.MonkeyPatch,
    action_input: RunActionInput,
    role: Role,
    template_depth: int,
    secret_backed: bool,
) -> service.PreparedContext:
    implementations = {
        f"testing.level{template_depth}": ActionImplementation(
            type="udf", action_name=f"testing.level{template_depth}"
        )
    }
    for level in range(template_depth):
        name = f"level{level}"
        implementations[f"testing.{name}"] = ActionImplementation(
            type="template",
            action_name=f"testing.{name}",
            template_definition={
                "name": name,
                "namespace": "testing",
                "title": name,
                "description": "Synthetic error boundary",
                "display_group": "Testing",
                "expects": {},
                "steps": [
                    {
                        "ref": "child",
                        "action": f"testing.level{level + 1}",
                        "args": {"value": "${{ inputs.value }}"},
                    }
                ],
                "returns": "${{ steps.child.result }}",
            },
        )
    resolved = ResolvedContext(
        secrets={"svc": {"TOKEN": CANARY}} if secret_backed else {},
        variables={},
        action_impl=implementations[action_input.task.action],
        evaluated_args={"value": CANARY if secret_backed else "plain"},
        workspace_id=str(role.workspace_id),
        workflow_id=str(action_input.run_context.wf_id),
        run_id=str(action_input.run_context.wf_run_id),
        executor_token="synthetic-token",
    )
    context = service.PreparedContext(
        resolved_context=resolved,
        mask_values={CANARY} if secret_backed else set(),
        provenance=build_provenance(action_input.task.args),
    )
    monkeypatch.setattr(service.registry_resolver, "prefetch_lock", AsyncMock())
    monkeypatch.setattr(
        service.registry_resolver,
        "resolve_action",
        AsyncMock(side_effect=lambda name, *_args: implementations[name]),
    )
    monkeypatch.setattr(
        service.registry_resolver,
        "collect_action_secrets_from_manifest",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        service, "_mint_action_executor_token", Mock(return_value="token")
    )
    monkeypatch.setattr(
        service, "prepare_resolved_context", AsyncMock(return_value=context)
    )
    return context


@pytest.mark.parametrize(
    ("make_error", "kind", "owner", "retry"),
    [
        (
            lambda: SandboxWorkloadError(
                CANARY, error_code=SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED
            ),
            RuntimeErrorKind.SANDBOX_RESOURCE_LIMIT_EXCEEDED,
            RuntimeErrorOwner.USER,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            lambda: SandboxWorkloadError(CANARY, error_code=SandboxErrorCode.TIMEOUT),
            RuntimeErrorKind.ACTION_EXECUTION_FAILED,
            RuntimeErrorOwner.USER,
            RetryDisposition.RETRYABLE,
        ),
        (
            lambda: RegistryArtifactCacheCapacityError(
                current_bytes=80, additional_bytes=30, max_bytes=100
            ),
            RuntimeErrorKind.EXECUTOR_REGISTRY_CAPACITY_EXHAUSTED,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            lambda: RegistryArtifactCacheLeaseContentionError(
                current_bytes=80, additional_bytes=30, max_bytes=100
            ),
            RuntimeErrorKind.EXECUTOR_REGISTRY_LEASE_CONTENTION,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
        ),
        (
            RegistryArtifactExtractionError,
            RuntimeErrorKind.EXECUTOR_REGISTRY_EXTRACTION_FAILED,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            lambda: SandboxInfrastructureError(CANARY),
            RuntimeErrorKind.EXECUTOR_SANDBOX_INFRASTRUCTURE_FAILED,
            RuntimeErrorOwner.PLATFORM,
            RetryDisposition.RETRYABLE,
        ),
        (
            lambda: ValueError(CANARY),
            RuntimeErrorKind.ACTION_EXECUTION_FAILED,
            RuntimeErrorOwner.USER,
            RetryDisposition.RETRYABLE,
        ),
    ],
)
async def test_backend_classification_survives_sanitization(
    action_input: RunActionInput,
    role: Role,
    secret_backed: bool,
    make_error: Callable[[], Exception],
    kind: RuntimeErrorKind,
    owner: RuntimeErrorOwner,
    retry: RetryDisposition,
) -> None:
    original = make_error()
    backend = Mock(execute=AsyncMock(side_effect=original))
    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            backend, action_input, service.DispatchActionContext(role)
        )

    error = caught.value
    classification = classify_execute_action_error(
        error, action_name=action_input.task.action
    )
    assert classification.kind is kind
    assert classification.owner is owner
    assert classification.retry_disposition is retry
    assert error.__cause__ is None
    assert error.__context__ is None
    if owner is RuntimeErrorOwner.PLATFORM:
        expected = classify_execute_action_error(
            original, action_name=action_input.task.action
        )
        assert classification.message == expected.message
        assert classification.message != str(original)
        assert "classification-canary" not in classification.model_dump_json()
        if secret_backed:
            assert error.info.message == classification.message
            assert "Details withheld" not in str(error)
    if secret_backed:
        assert CANARY not in str(error)
        assert "classification-canary" not in classification.model_dump_json()
        if owner is RuntimeErrorOwner.USER:
            assert "Details withheld" in classification.message
        # Exercise both raw Python failure conversion and the classified transport.
        for transported in (
            error,
            application_error_from_classification(classification),
        ):
            failure = Failure()
            await DataConverter.default.encode_failure(transported, failure)
            assert b"classification-canary" not in failure.SerializeToString()
            assert not failure.HasField("cause")


async def test_prefetch_entitlement_remains_non_retryable(
    monkeypatch: pytest.MonkeyPatch, action_input: RunActionInput, role: Role
) -> None:
    monkeypatch.setattr(
        service.registry_resolver,
        "prefetch_lock",
        AsyncMock(side_effect=EntitlementRequired("synthetic_feature")),
    )
    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            Mock(), action_input, service.DispatchActionContext(role)
        )
    classification = classify_execute_action_error(
        caught.value, action_name=action_input.task.action
    )
    assert classification.kind is RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.cause_type == "EntitlementRequired"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_backend_diagnostic_type_cannot_control_retry_policy(
    action_input: RunActionInput, role: Role
) -> None:
    result = ExecutorResultFailure(
        error=ExecutorActionErrorInfo(
            action_name=action_input.task.action,
            type="EntitlementRequired",
            message="Synthetic action-controlled diagnostic",
            filename="action.py",
            function="run",
        )
    )
    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            Mock(execute=AsyncMock(return_value=result)),
            action_input,
            service.DispatchActionContext(role),
        )
    classification = classify_execute_action_error(
        caught.value, action_name=action_input.task.action
    )
    assert classification.kind is RuntimeErrorKind.ACTION_EXECUTION_FAILED
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.retry_disposition is RetryDisposition.RETRYABLE


async def test_preserved_metadata_cannot_restore_an_old_plaintext_message(
    action_input: RunActionInput, role: Role
) -> None:
    original = ExecutionError(
        info=ExecutorActionErrorInfo(
            action_name=action_input.task.action,
            type="EntitlementRequired",
            message="Safe diagnostic",
            filename="executor.py",
            function="run",
        ),
        classification=RuntimeErrorClassification.user(
            kind=RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
            message=CANARY,
            retry_disposition=RetryDisposition.NON_RETRYABLE,
        ),
    )
    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            Mock(execute=AsyncMock(side_effect=original)),
            action_input,
            service.DispatchActionContext(role),
        )
    classification = classify_execute_action_error(
        caught.value, action_name=action_input.task.action
    )
    assert classification.kind is RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert classification.message == caught.value.info.message
    assert "classification-canary" not in classification.model_dump_json()
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_sanitized_mixed_loop_preserves_ownership_and_retry_policy(
    action_input: RunActionInput, role: Role
) -> None:
    errors: list[ExecutionError] = []
    for iteration, cause in enumerate(
        [
            RegistryArtifactCacheLeaseContentionError(
                current_bytes=80, additional_bytes=30, max_bytes=100
            ),
            SandboxWorkloadError(
                CANARY, error_code=SandboxErrorCode.RESOURCE_LIMIT_EXCEEDED
            ),
        ]
    ):
        with pytest.raises(ExecutionError) as caught:
            await service.invoke_once(
                Mock(execute=AsyncMock(side_effect=cause)),
                action_input,
                service.DispatchActionContext(role),
                iteration=iteration,
            )
        errors.append(caught.value)
        assert caught.value.info.loop_iteration == iteration
        assert caught.value.info.loop_vars is None
    classification = classify_execute_action_error(
        LoopExecutionError(errors), action_name=action_input.task.action
    )
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.EXECUTOR_REGISTRY_LEASE_CONTENTION
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
