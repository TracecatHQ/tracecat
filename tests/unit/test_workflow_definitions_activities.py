from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    EntitlementRequired,
    RegistryLockInvalidDataError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import extract_error_classification
from tracecat.workflow.management.definitions import (
    WorkflowDefinitionsService,
    get_workflow_definition_activity,
    resolve_registry_lock_activity,
)
from tracecat.workflow.management.schemas import (
    GetWorkflowDefinitionActivityInputs,
    ResolveRegistryLockActivityInputs,
)


@pytest.fixture
def mock_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


def _valid_definition_content() -> dict[str, object]:
    return {
        "title": "Valid definition",
        "description": "",
        "entrypoint": {"ref": "start"},
        "actions": [{"ref": "start", "action": "core.noop", "args": {}}],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "registry_lock"),
    [
        ({"title": "malformed persisted definition"}, None),
        (
            _valid_definition_content(),
            {"origins": {}, "actions": {"core.noop": "missing_origin"}},
        ),
        (_valid_definition_content(), {}),
    ],
    ids=["dsl", "registry-lock", "empty-registry-lock"],
)
async def test_get_workflow_definition_activity_classifies_invalid_persisted_data(
    mock_role: Role,
    content: dict[str, object],
    registry_lock: dict[str, object] | None,
) -> None:
    inputs = GetWorkflowDefinitionActivityInputs(
        role=mock_role,
        workflow_id=WorkflowUUID.new_uuid4(),
    )
    mock_service = AsyncMock(spec=WorkflowDefinitionsService)
    mock_service.get_definition_by_workflow_id.return_value = SimpleNamespace(
        content=content,
        registry_lock=registry_lock,
    )
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service

    with patch(
        "tracecat.workflow.management.definitions.WorkflowDefinitionsService.with_session",
        return_value=mock_ctx,
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await get_workflow_definition_activity(inputs)

    app_error = exc_info.value
    classification = extract_error_classification(app_error)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_DEFINITION_INVALID_DATA
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert app_error.non_retryable is True


def _manifest_validation_error() -> ValidationError:
    try:
        RegistryVersionManifest.model_validate(
            {
                "schema_version": "v1",
                "actions": [{"namespace": "tools.invalid"}],
            }
        )
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected manifest validation to fail")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("make_error", "owner", "kind", "retry_disposition", "detail"),
    [
        pytest.param(
            lambda: EntitlementRequired("custom_registry"),
            RuntimeErrorOwner.USER,
            RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
            RetryDisposition.NON_RETRYABLE,
            {"entitlement": "custom_registry"},
            id="entitlement_error",
        ),
        pytest.param(
            lambda: BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            ),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE,
            RetryDisposition.RETRYABLE,
            {"origin": "tracecat_registry"},
            id="builtin_sync_pending_retryable",
        ),
        pytest.param(
            lambda: RegistryLockInvalidDataError(
                "action is not present in the selected registry"
            ),
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA,
            RetryDisposition.NON_RETRYABLE,
            None,
            id="invalid_registry_terminal",
        ),
        pytest.param(
            _manifest_validation_error,
            RuntimeErrorOwner.PLATFORM,
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA,
            RetryDisposition.NON_RETRYABLE,
            None,
            id="invalid_manifest_terminal",
        ),
    ],
)
async def test_resolve_registry_lock_activity_maps_error(
    mock_role: Role,
    make_error: Callable[[], Exception],
    owner: RuntimeErrorOwner,
    kind: RuntimeErrorKind,
    retry_disposition: RetryDisposition,
    detail: dict[str, str] | None,
) -> None:
    inputs = ResolveRegistryLockActivityInputs(
        role=mock_role,
        action_names={"tools.custom.only_action"},
    )
    mock_service = AsyncMock()
    mock_service.resolve_lock_with_bindings.side_effect = make_error()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service

    with patch(
        "tracecat.workflow.management.definitions.RegistryLockService.with_session",
        return_value=mock_ctx,
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await resolve_registry_lock_activity(inputs)

    app_error = exc_info.value
    classification = extract_error_classification(app_error)
    assert classification is not None
    assert classification.owner is owner
    assert classification.kind is kind
    assert classification.retry_disposition is retry_disposition
    assert app_error.non_retryable is (
        retry_disposition is RetryDisposition.NON_RETRYABLE
    )
    if detail is not None:
        assert len(app_error.details) > 0
        app_detail = app_error.details[0]
        assert isinstance(app_detail, dict)
        assert detail.items() <= app_detail.items()


@pytest.mark.anyio
async def test_resolve_registry_lock_activity_classifies_unexpected_failure(
    mock_role: Role,
) -> None:
    diagnostic = "registry diagnostic must not enter history"
    inputs = ResolveRegistryLockActivityInputs(
        role=mock_role,
        action_names={"tools.custom.only_action"},
    )
    mock_service = AsyncMock()
    mock_service.resolve_lock_with_bindings.side_effect = RuntimeError(diagnostic)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service

    with patch(
        "tracecat.workflow.management.definitions.RegistryLockService.with_session",
        return_value=mock_ctx,
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await resolve_registry_lock_activity(inputs)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert diagnostic not in str(exc_info.value)
