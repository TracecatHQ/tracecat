import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
import sentry_sdk
from pydantic import SecretStr
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretType,
)

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import ExecutionError, TracecatCredentialsError
from tracecat.executor import service as executor_service
from tracecat.executor.error_policy import classify_execute_action_error
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.executor.service import prepare_resolved_context
from tracecat.expressions.policy import build_provenance
from tracecat.identifiers import InternalServiceID
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.integrations.enums import OAuthGrantType
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.sandbox.service import SandboxService
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import ctx_unsafe_disable_secret_error_withholding
from tracecat.secrets.constants import MASK_VALUE


def test_flatten_secrets_supports_runtime_scalar_entries() -> None:
    flattened = secrets_manager.flatten_secrets(
        {
            "aws": {"AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role"},
            "TRACECAT_AWS_EXTERNAL_ID": "11111111111111111111111111111111",
        }
    )

    assert flattened == {
        "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
        "TRACECAT_AWS_EXTERNAL_ID": "11111111111111111111111111111111",
    }


def test_flatten_secrets_rejects_runtime_scalar_key_collisions() -> None:
    with pytest.raises(ValueError, match="TRACECAT_AWS_EXTERNAL_ID"):
        secrets_manager.flatten_secrets(
            {
                "aws": {"TRACECAT_AWS_EXTERNAL_ID": "customer-value"},
                "TRACECAT_AWS_EXTERNAL_ID": "11111111111111111111111111111111",
            }
        )


@pytest.mark.anyio
async def test_get_action_secrets_passes_sets_to_auth_sandbox(mocker):
    """Test that get_action_secrets correctly passes secrets as sets to AuthSandbox."""
    # Create registry secrets with both required and optional
    action_secrets: set[RegistrySecretType] = {
        RegistrySecret(name="required_secret1", keys=["REQ_KEY1"], optional=False),
        RegistrySecret(name="required_secret2", keys=["REQ_KEY2"], optional=False),
        RegistrySecret(name="optional_secret1", keys=["OPT_KEY1"], optional=True),
        RegistrySecret(name="optional_secret2", keys=["OPT_KEY2"], optional=True),
    }

    # Mock templated secrets from args
    mocker.patch(
        "tracecat.expressions.eval.extract_templated_secrets",
        return_value=["args_secret1", "args_secret2"],
    )
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    # Mock AuthSandbox to capture call arguments
    mock_sandbox = mocker.MagicMock()
    mock_sandbox.secrets = {}
    mock_sandbox.__aenter__.return_value = mock_sandbox
    mock_sandbox.__aexit__.return_value = None

    auth_sandbox_mock = mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox")
    auth_sandbox_mock.return_value = mock_sandbox

    # Run the function
    await secrets_manager.get_action_secrets(
        secret_exprs={"args_secret1", "args_secret2"}, action_secrets=action_secrets
    )

    # Verify AuthSandbox was called with sets, not lists
    auth_sandbox_mock.assert_called_once()
    _call_args, call_kwargs = auth_sandbox_mock.call_args

    # Verify that secrets parameter is a set
    assert isinstance(call_kwargs["secrets"], set)
    expected_secrets = {
        "required_secret1",
        "required_secret2",
        "optional_secret1",
        "optional_secret2",
        "args_secret1",
        "args_secret2",
    }
    assert call_kwargs["secrets"] == expected_secrets

    # Verify that optional_secrets parameter is a set
    assert isinstance(call_kwargs["optional_secrets"], set)
    expected_optional_secrets = {"optional_secret1", "optional_secret2"}
    assert call_kwargs["optional_secrets"] == expected_optional_secrets

    # Verify environment parameter
    assert call_kwargs["environment"] == "test_env"


@pytest.mark.anyio
async def test_get_action_secrets_skips_optional_oauth(mocker):
    """Ensure optional OAuth integrations do not raise when missing."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        ),
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="client_credentials",
            optional=True,
        ),
    }

    mocker.patch("tracecat.expressions.eval.extract_templated_secrets", return_value=[])
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)

    delegated_integration = mocker.MagicMock()
    delegated_integration.provider_id = "azure_log_analytics"
    delegated_integration.grant_type = OAuthGrantType.AUTHORIZATION_CODE

    service = mocker.AsyncMock()
    service.list_integrations.return_value = [delegated_integration]
    service.refresh_token_if_needed.return_value = delegated_integration
    service.get_access_token.return_value = SecretStr("user-token")

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=set(), action_secrets=action_secrets
    )
    assert (
        secrets["azure_log_analytics_oauth"]["AZURE_LOG_ANALYTICS_USER_TOKEN"]
        == "user-token"
    )
    assert (
        "AZURE_LOG_ANALYTICS_SERVICE_TOKEN" not in secrets["azure_log_analytics_oauth"]
    )


@pytest.mark.parametrize("secret_name", ["aws", "amazon_bedrock"])
@pytest.mark.anyio
async def test_get_action_secrets_injects_runtime_aws_external_id(
    mocker, secret_name: str
):
    action_secrets: set[RegistrySecretType] = {
        RegistrySecret(name=secret_name, keys=["AWS_ROLE_ARN"], optional=False),
    }
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {
        secret_name: {
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
        }
    }
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)
    mocker.patch(
        "tracecat.secrets.secrets_manager.build_workspace_external_id",
        return_value="tracecat-ws-deadbeef",
    )

    token = secrets_manager.ctx_role.set(
        Role(
            type="service",
            workspace_id=UUID("11111111-1111-1111-1111-111111111111"),
            service_id="tracecat-executor",
        )
    )
    try:
        secrets = await secrets_manager.get_action_secrets(
            secret_exprs=set(), action_secrets=action_secrets
        )
    finally:
        secrets_manager.ctx_role.reset(token)

    assert secrets["TRACECAT_AWS_EXTERNAL_ID"] == "tracecat-ws-deadbeef"


@pytest.mark.anyio
async def test_get_action_secrets_merges_multiple_oauth_tokens(mocker):
    """Ensure both delegated and service tokens are returned when available."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        ),
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="client_credentials",
            optional=True,
        ),
    }

    mocker.patch("tracecat.expressions.eval.extract_templated_secrets", return_value=[])
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)

    delegated_integration = mocker.MagicMock()
    delegated_integration.provider_id = "azure_log_analytics"
    delegated_integration.grant_type = OAuthGrantType.AUTHORIZATION_CODE

    service_integration = mocker.MagicMock()
    service_integration.provider_id = "azure_log_analytics"
    service_integration.grant_type = OAuthGrantType.CLIENT_CREDENTIALS

    service = mocker.AsyncMock()
    service.list_integrations.return_value = [
        delegated_integration,
        service_integration,
    ]
    service.refresh_token_if_needed.side_effect = lambda integration: integration

    def _get_access_token(integration):
        if integration.grant_type == OAuthGrantType.AUTHORIZATION_CODE:
            return SecretStr("user-token")
        if integration.grant_type == OAuthGrantType.CLIENT_CREDENTIALS:
            return SecretStr("service-token")
        return None

    service.get_access_token.side_effect = _get_access_token

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=set(), action_secrets=action_secrets
    )
    assert (
        secrets["azure_log_analytics_oauth"]["AZURE_LOG_ANALYTICS_USER_TOKEN"]
        == "user-token"
    )
    assert (
        secrets["azure_log_analytics_oauth"]["AZURE_LOG_ANALYTICS_SERVICE_TOKEN"]
        == "service-token"
    )


@pytest.mark.anyio
async def test_get_action_secrets_missing_required_oauth_raises(mocker):
    """Required OAuth integrations should surface a credentials error."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        )
    }

    mocker.patch("tracecat.expressions.eval.extract_templated_secrets", return_value=[])
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)

    service = mocker.AsyncMock()
    service.list_integrations.return_value = []

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    with pytest.raises(TracecatCredentialsError):
        await secrets_manager.get_action_secrets(
            secret_exprs=set(), action_secrets=action_secrets
        )


@pytest.mark.anyio
async def test_extract_templated_secrets_detects_nested_complex_expressions():
    from tracecat.expressions.eval import extract_templated_secrets

    expr = '${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + "/token:" + SECRETS.zendesk.ZENDESK_API_TOKEN) }}'
    secrets = extract_templated_secrets(expr)
    assert sorted(secrets) == sorted(
        [
            "zendesk.ZENDESK_EMAIL",
            "zendesk.ZENDESK_API_TOKEN",
        ]
    )


@pytest.mark.anyio
async def test_invoke_once_offloads_root_secret_masking(mocker):
    role = Role(
        type="service",
        organization_id=UUID(int=1),
        service_id="tracecat-executor",
    )
    action_input = mocker.Mock()
    action_input.task.action = "core.transform.reshape"
    action_input.registry_lock = {}
    action_input.exec_context = {}
    action_result = {"value": "secret"}
    masked_result = {"value": "***"}
    resolved_context = mocker.Mock(logical_time=mocker.sentinel.logical_time)
    prepared_context = executor_service.PreparedContext(
        resolved_context=resolved_context,
        mask_values={"secret"},
    )

    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    mocker.patch.object(
        executor_service,
        "_invoke_step",
        new=mocker.AsyncMock(return_value=action_result),
    )
    to_thread = mocker.patch.object(
        executor_service.asyncio,
        "to_thread",
        new=mocker.AsyncMock(return_value=masked_result),
    )

    result = await executor_service.invoke_once(
        backend=mocker.Mock(),
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
    )

    assert result == masked_result
    to_thread.assert_awaited_once_with(
        executor_service.apply_masks_object,
        action_result,
        masks={"secret"},
    )


def _expression_policy_role(service_id: InternalServiceID) -> Role:
    return Role(
        type="service",
        organization_id=UUID(int=1),
        workspace_id=UUID(int=2),
        service_id=service_id,
    )


def _expression_policy_input(
    action_name: str, args: Mapping[str, object]
) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(ref="a", action=action_name, args=args),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=generate_exec_id(wf_id),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "v1"},
            actions={action_name: "tracecat_registry"},
        ),
    )


def _patch_expression_policy_resolution(
    mocker,
    *,
    action_name: str,
    action_secrets: set[RegistrySecretType],
    fetched_secrets: dict[str, dict[str, str]],
    workspace_variables: dict[str, dict[str, str]],
):
    """Stub registry and credential IO around argument expression handling."""
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            return_value=ActionImplementation(
                type="udf",
                action_name=action_name,
                module="tracecat_registry.integrations.core.transform",
                name="reshape",
            )
        ),
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "collect_action_secrets_from_manifest",
        new=mocker.AsyncMock(return_value=action_secrets),
    )
    get_action_secrets = mocker.patch.object(
        executor_service.secrets_manager,
        "get_action_secrets",
        new=mocker.AsyncMock(return_value=fetched_secrets),
    )
    get_workspace_variables = mocker.patch.object(
        executor_service,
        "get_workspace_variables",
        new=mocker.AsyncMock(return_value=workspace_variables),
    )
    mocker.patch.object(
        executor_service,
        "_mint_action_executor_token",
        return_value="token",
    )
    project_secret_env = mocker.patch.object(
        executor_service,
        "project_secret_env",
        new=mocker.AsyncMock(
            return_value=SecretEnvProjection(
                env={"TOKEN": "runtime-secret"},
                mask_values={"runtime-secret"},
            )
        ),
    )
    return get_action_secrets, get_workspace_variables, project_secret_env


@pytest.mark.parametrize("service_id", ["tracecat-executor", "tracecat-mcp"])
@pytest.mark.parametrize(
    ("action_name", "preserved_parameter", "runtime_parameter"),
    [
        ("core.workflow.edit_workflow", "patch_ops", "workflow_id"),
        ("core.workflow.create_workflow", "definition_yaml", "unmapped_parameter"),
    ],
)
@pytest.mark.anyio
async def test_prepare_resolved_context_preserves_only_mapped_parameter(
    mocker,
    service_id: InternalServiceID,
    action_name: str,
    preserved_parameter: str,
    runtime_parameter: str,
):
    """Mapped workflow source stays literal for workflow and agent callers."""
    preserved_source: object
    if preserved_parameter == "patch_ops":
        preserved_source = [
            {
                "op": "add",
                "path": "/definition/actions/-",
                "value": {
                    "${{ VARS.source.key }}": [
                        "${{ SECRETS.source.TOKEN }}",
                        "${{ FN.now() }}",
                    ]
                },
            }
        ]
    else:
        preserved_source = (
            "definition:\n"
            "  actions:\n"
            "    - args:\n"
            "        token: ${{ SECRETS.source.TOKEN }}\n"
            "        generated_at: ${{ FN.now() }}\n"
        )
    args = {
        runtime_parameter: ("${{ SECRETS.runtime.TOKEN }}:${{ VARS.runtime.value }}"),
        preserved_parameter: preserved_source,
    }
    action_secrets: set[RegistrySecretType] = {
        RegistrySecret(name="declared", keys=["KEY"], optional=False)
    }
    get_action_secrets, get_workspace_variables, _ = (
        _patch_expression_policy_resolution(
            mocker,
            action_name=action_name,
            action_secrets=action_secrets,
            fetched_secrets={
                "runtime": {"TOKEN": "runtime-secret"},
                "declared": {"KEY": "declared-secret"},
            },
            workspace_variables={"runtime": {"value": "runtime-variable"}},
        )
    )
    mocker.patch.object(
        executor_service.config,
        "TRACECAT__UNSAFE_DISABLE_SM_MASKING",
        False,
    )

    prepared = await prepare_resolved_context(
        input=_expression_policy_input(action_name, args),
        role=_expression_policy_role(service_id),
    )

    assert prepared.resolved_context.evaluated_args == {
        runtime_parameter: "runtime-secret:runtime-variable",
        preserved_parameter: preserved_source,
    }
    assert get_action_secrets.await_args.kwargs == {
        "secret_exprs": {"runtime.TOKEN"},
        "action_secrets": action_secrets,
    }
    assert get_workspace_variables.await_args.kwargs["variable_exprs"] == {"runtime"}
    # Masks union the raw fetched secrets (derived before argument evaluation so
    # they exist when it raises) with the projection's own masks. "declared-secret"
    # is fetched into the evaluation context, so it must be masked even though the
    # stubbed projection above only reports "runtime-secret".
    assert prepared.mask_values == {"runtime-secret", "declared-secret"}


@pytest.mark.parametrize("service_id", ["tracecat-executor", "tracecat-mcp"])
@pytest.mark.anyio
async def test_prepare_resolved_context_redacts_secrets_before_collection(
    mocker,
    service_id: InternalServiceID,
):
    """Durable content resolves safe expressions without fetching direct secrets."""
    action_name = "core.cases.create_comment"
    action_secrets: set[RegistrySecretType] = {
        RegistrySecret(name="declared", keys=["KEY"], optional=False)
    }
    get_action_secrets, get_workspace_variables, _ = (
        _patch_expression_policy_resolution(
            mocker,
            action_name=action_name,
            action_secrets=action_secrets,
            fetched_secrets={
                "runtime": {"TOKEN": "runtime-secret"},
                "declared": {"KEY": "declared-secret"},
            },
            workspace_variables={"runtime": {"value": "api.example.com"}},
        )
    )
    args = {
        "case_id": "${{ SECRETS.runtime.TOKEN }}",
        "content": (
            "Host: ${{ VARS.runtime.value }}, "
            "token: ${{ SECRETS.source.TOKEN }}, "
            "encoded: "
            "${{ FN.to_base64(SECRETS.source.TOKEN + VARS.source.suffix) }}"
        ),
    }

    prepared = await prepare_resolved_context(
        input=_expression_policy_input(action_name, args),
        role=_expression_policy_role(service_id),
    )

    assert prepared.resolved_context.evaluated_args == {
        "case_id": "runtime-secret",
        "content": (
            f"Host: api.example.com, token: {MASK_VALUE}, encoded: {MASK_VALUE}"
        ),
    }
    assert get_action_secrets.await_args.kwargs == {
        "secret_exprs": {"runtime.TOKEN"},
        "action_secrets": action_secrets,
    }
    assert get_workspace_variables.await_args.kwargs["variable_exprs"] == {"runtime"}


@pytest.mark.parametrize(
    ("action_name", "parameter"),
    [
        ("core.transform.reshape", "patch_ops"),
        ("core.transform.reshape", "content"),
        ("core.transform.reshape", "title"),
        ("core.workflow.edit_workflow", "workflow_id"),
    ],
)
@pytest.mark.anyio
async def test_prepare_resolved_context_resolves_unmapped_parameters(
    mocker,
    action_name: str,
    parameter: str,
):
    """Policy matching requires the exact action and parameter pair."""
    get_action_secrets, get_workspace_variables, _ = (
        _patch_expression_policy_resolution(
            mocker,
            action_name=action_name,
            action_secrets=set(),
            fetched_secrets={"runtime": {"TOKEN": "runtime-secret"}},
            workspace_variables={},
        )
    )
    args = {parameter: "${{ SECRETS.runtime.TOKEN }}"}

    prepared = await prepare_resolved_context(
        input=_expression_policy_input(action_name, args),
        role=_expression_policy_role("tracecat-executor"),
    )

    assert prepared.resolved_context.evaluated_args == {parameter: "runtime-secret"}
    assert get_action_secrets.await_args.kwargs["secret_exprs"] == {"runtime.TOKEN"}
    assert get_workspace_variables.await_args.kwargs["variable_exprs"] == set()


@pytest.mark.parametrize(
    ("action_name", "parameter"),
    [
        ("core.http_poll", "headers"),
        ("core.http_request", "auth"),
        ("core.http_request", "headers"),
        ("core.http_request", "params"),
    ],
)
@pytest.mark.anyio
async def test_prepare_resolved_context_resolves_explicit_parameters(
    mocker,
    action_name: str,
    parameter: str,
):
    get_action_secrets, get_workspace_variables, _ = (
        _patch_expression_policy_resolution(
            mocker,
            action_name=action_name,
            action_secrets=set(),
            fetched_secrets={"runtime": {"TOKEN": "runtime-secret"}},
            workspace_variables={},
        )
    )
    args = {parameter: "${{ SECRETS.runtime.TOKEN }}"}

    prepared = await prepare_resolved_context(
        input=_expression_policy_input(action_name, args),
        role=_expression_policy_role("tracecat-executor"),
    )

    assert prepared.resolved_context.evaluated_args == {parameter: "runtime-secret"}
    assert get_action_secrets.await_args.kwargs["secret_exprs"] == {"runtime.TOKEN"}
    assert get_workspace_variables.await_args.kwargs["variable_exprs"] == set()


def _policy_source_provenance(args: dict[str, object]):
    return build_provenance(args)


@pytest.mark.parametrize(
    ("step_action", "step_args", "source_value", "evaluated_value", "expected_args"),
    [
        (
            "core.cases.create_comment",
            {
                "case_id": "case-123",
                "content": ("Host ${{ VARS.runtime.host }}, token ${{ inputs.value }}"),
            },
            "${{ SECRETS.runtime.TOKEN }}",
            "runtime-secret",
            {
                "case_id": "case-123",
                "content": f"Host api.example.com, token {MASK_VALUE}",
            },
        ),
        (
            "core.cases.create_comment",
            {
                "case_id": "case-123",
                "content": '${{ inputs.value || "fallback" }}',
            },
            "${{ SECRETS.runtime.TOKEN }}",
            "runtime-secret",
            {
                "case_id": "case-123",
                "content": MASK_VALUE,
            },
        ),
        (
            "core.cases.create_comment",
            {
                "case_id": "case-123",
                "content": "${{ inputs.value }}",
            },
            "${{ ACTIONS.fetch.result.body }}",
            "upstream-value",
            {
                "case_id": "case-123",
                "content": "upstream-value",
            },
        ),
        (
            "core.workflow.edit_workflow",
            {
                "workflow_id": "wf-123",
                "patch_ops": "${{ inputs.value }}",
            },
            [
                {
                    "op": "add",
                    "path": "/definition/actions/-",
                    "value": "${{ SECRETS.runtime.TOKEN }}",
                }
            ],
            [
                {
                    "op": "add",
                    "path": "/definition/actions/-",
                    "value": "runtime-secret",
                }
            ],
            {
                "workflow_id": "wf-123",
                "patch_ops": [
                    {
                        "op": "add",
                        "path": "/definition/actions/-",
                        "value": "${{ SECRETS.runtime.TOKEN }}",
                    }
                ],
            },
        ),
    ],
)
@pytest.mark.anyio
async def test_template_step_applies_target_action_expression_policy(
    mocker,
    step_action: str,
    step_args: dict[str, object],
    source_value: object,
    evaluated_value: object,
    expected_args: dict[str, object],
):
    """Template input source reaches the target action's policy boundary."""
    template_action = "testing.policy_wrapper"
    action_input = _expression_policy_input(
        template_action,
        {"value": source_value},
    )
    role = _expression_policy_role("tracecat-executor")
    parent_resolved = ResolvedContext(
        secrets={"runtime": {"TOKEN": "runtime-secret"}},
        variables={"runtime": {"host": "api.example.com"}},
        action_impl=ActionImplementation(
            type="template",
            action_name=template_action,
            template_definition={
                "name": "policy_wrapper",
                "namespace": "testing",
                "title": "Policy wrapper",
                "description": "Exercises a protected sink",
                "display_group": "Testing",
                "expects": {},
                "steps": [
                    {
                        "ref": "persist",
                        "action": step_action,
                        "args": step_args,
                    }
                ],
                "returns": "${{ steps.persist.result }}",
            },
        ),
        evaluated_args={"value": evaluated_value},
        workspace_id=str(role.workspace_id),
        workflow_id=str(action_input.run_context.wf_id),
        run_id=str(action_input.run_context.wf_run_id),
        executor_token="parent-token",
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            return_value=ActionImplementation(type="udf", action_name=step_action)
        ),
    )
    mocker.patch.object(
        executor_service,
        "_mint_action_executor_token",
        return_value="step-token",
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(
        return_value=ExecutorResultSuccess(result={"persisted": True})
    )

    result = await executor_service._execute_template_action(
        backend=backend,
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
        resolved_context=parent_resolved,
        timeout=30,
        provenance=_policy_source_provenance({"value": source_value}),
    )

    assert result == {"persisted": True}
    step_resolved = backend.execute.await_args.kwargs["resolved_context"]
    assert step_resolved.evaluated_args == expected_args


@pytest.mark.anyio
async def test_template_step_result_is_not_tainted_by_its_arguments(mocker):
    """Step results stay runtime data across the accepted implementation boundary."""
    source_value = "${{ SECRETS.runtime.TOKEN }}"
    action_input = _expression_policy_input(
        "testing.policy_wrapper",
        {"value": source_value},
    )
    role = _expression_policy_role("tracecat-executor")
    parent_resolved = _policy_wrapper_resolved(
        action_input,
        role,
        steps=[
            {
                "ref": "normalize",
                "action": "core.transform.reshape",
                "args": {"value": "${{ inputs.value }}"},
            },
            {
                "ref": "persist",
                "action": "core.cases.create_comment",
                "args": {
                    "case_id": "case-123",
                    "content": "${{ steps.normalize.result }}",
                },
            },
        ],
        evaluated_args={"value": "runtime-secret"},
        variables={},
        secrets={"runtime": {"TOKEN": "runtime-secret"}},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            side_effect=[
                ActionImplementation(type="udf", action_name="core.transform.reshape"),
                ActionImplementation(
                    type="udf", action_name="core.cases.create_comment"
                ),
            ]
        ),
    )
    mocker.patch.object(
        executor_service,
        "_mint_action_executor_token",
        return_value="step-token",
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(
        side_effect=[
            ExecutorResultSuccess(result="runtime-secret"),
            ExecutorResultSuccess(result={"persisted": True}),
        ]
    )

    await executor_service._execute_template_action(
        backend=backend,
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
        resolved_context=parent_resolved,
        timeout=30,
        provenance=_policy_source_provenance({"value": source_value}),
    )

    sink_resolved = backend.execute.await_args_list[1].kwargs["resolved_context"]
    assert sink_resolved.evaluated_args == {
        "case_id": "case-123",
        "content": "runtime-secret",
    }


@pytest.mark.anyio
async def test_compound_secret_dependency_reaches_nested_template_sink(mocker):
    """Secret dependency survives composition and a nested template boundary."""
    source_value = "${{ SECRETS.runtime.TOKEN }}"
    action_input = _expression_policy_input(
        "testing.policy_wrapper",
        {"value": source_value},
    )
    role = _expression_policy_role("tracecat-executor")
    inner_template = ActionImplementation(
        type="template",
        action_name="testing.inner_wrapper",
        template_definition={
            "name": "inner_wrapper",
            "namespace": "testing",
            "title": "Inner wrapper",
            "description": "Calls a protected sink",
            "display_group": "Testing",
            "expects": {},
            "steps": [
                {
                    "ref": "persist",
                    "action": "core.cases.create_comment",
                    "args": {
                        "case_id": "case-123",
                        "content": "${{ inputs.value }}",
                    },
                }
            ],
            "returns": "done",
        },
    )
    parent_resolved = _policy_wrapper_resolved(
        action_input,
        role,
        steps=[
            {
                "ref": "inner",
                "action": "testing.inner_wrapper",
                "args": {
                    "value": '${{ inputs.value || "fallback" }}',
                },
            }
        ],
        evaluated_args={"value": "runtime-secret"},
        variables={},
        secrets={"runtime": {"TOKEN": "runtime-secret"}},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            side_effect=[
                inner_template,
                ActionImplementation(
                    type="udf",
                    action_name="core.cases.create_comment",
                ),
            ]
        ),
    )
    mocker.patch.object(
        executor_service,
        "_mint_action_executor_token",
        return_value="step-token",
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(
        return_value=ExecutorResultSuccess(result={"persisted": True})
    )

    await executor_service._execute_template_action(
        backend=backend,
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
        resolved_context=parent_resolved,
        timeout=30,
        provenance=_policy_source_provenance({"value": source_value}),
    )

    sink_resolved = backend.execute.await_args.kwargs["resolved_context"]
    assert sink_resolved.evaluated_args == {
        "case_id": "case-123",
        "content": MASK_VALUE,
    }


def _policy_wrapper_resolved(
    action_input: RunActionInput,
    role: Role,
    *,
    steps: list[dict[str, object]],
    evaluated_args: dict[str, object],
    variables: dict[str, dict[str, str]],
    secrets: dict[str, dict[str, str]] | None = None,
    expects: dict[str, object] | None = None,
) -> ResolvedContext:
    return ResolvedContext(
        secrets=secrets or {},
        variables=variables,
        action_impl=ActionImplementation(
            type="template",
            action_name="testing.policy_wrapper",
            template_definition={
                "name": "policy_wrapper",
                "namespace": "testing",
                "title": "Policy wrapper",
                "description": "Exercises a protected sink",
                "display_group": "Testing",
                "expects": expects or {},
                "steps": steps,
                "returns": "done",
            },
        ),
        evaluated_args=evaluated_args,
        workspace_id=str(role.workspace_id),
        workflow_id=str(action_input.run_context.wf_id),
        run_id=str(action_input.run_context.wf_run_id),
        executor_token="parent-token",
    )


@pytest.mark.anyio
async def test_template_step_result_stays_inert_in_redact_parameter(mocker):
    """Materialized step results are grafted as data, never re-expanded as source."""
    action_input = _expression_policy_input("testing.policy_wrapper", {})
    role = _expression_policy_role("tracecat-executor")
    parent_resolved = _policy_wrapper_resolved(
        action_input,
        role,
        steps=[
            {
                "ref": "fetch",
                "action": "core.transform.reshape",
                "args": {"value": "external"},
            },
            {
                "ref": "persist",
                "action": "core.cases.create_comment",
                "args": {
                    "case_id": "case-123",
                    "content": "Summary: ${{ steps.fetch.result.note }}",
                },
            },
        ],
        evaluated_args={},
        variables={"runtime": {"host": "api.example.com"}},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            return_value=ActionImplementation(
                type="udf", action_name="core.transform.reshape"
            )
        ),
    )
    mocker.patch.object(
        executor_service, "_mint_action_executor_token", return_value="step-token"
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(
        side_effect=[
            ExecutorResultSuccess(result={"note": "${{ VARS.runtime.host }}"}),
            ExecutorResultSuccess(result={"persisted": True}),
        ]
    )

    await executor_service._execute_template_action(
        backend=backend,
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
        resolved_context=parent_resolved,
        timeout=30,
        provenance={},
    )

    persist_resolved = backend.execute.await_args_list[1].kwargs["resolved_context"]
    assert persist_resolved.evaluated_args == {
        "case_id": "case-123",
        "content": "Summary: ${{ VARS.runtime.host }}",
    }


@pytest.mark.anyio
async def test_omitted_template_default_seeds_preserve_provenance(mocker):
    default_ops = [
        {
            "op": "add",
            "path": "/definition/actions/-",
            "value": "${{ SECRETS.runtime.TOKEN }}",
        }
    ]
    action_input = _expression_policy_input("testing.policy_wrapper", {})
    role = _expression_policy_role("tracecat-executor")
    parent_resolved = _policy_wrapper_resolved(
        action_input,
        role,
        steps=[
            {
                "ref": "persist",
                "action": "core.workflow.edit_workflow",
                "args": {
                    "workflow_id": "wf-123",
                    "patch_ops": "${{ inputs.ops }}",
                },
            }
        ],
        evaluated_args={},
        variables={},
        expects={"ops": {"type": "Any", "default": default_ops}},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            return_value=ActionImplementation(
                type="udf",
                action_name="core.workflow.edit_workflow",
            )
        ),
    )
    mocker.patch.object(
        executor_service,
        "_mint_action_executor_token",
        return_value="step-token",
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(
        return_value=ExecutorResultSuccess(result={"persisted": True})
    )

    await executor_service._execute_template_action(
        backend=backend,
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
        resolved_context=parent_resolved,
        timeout=30,
        provenance={},
    )

    step_resolved = backend.execute.await_args.kwargs["resolved_context"]
    assert step_resolved.evaluated_args == {
        "workflow_id": "wf-123",
        "patch_ops": default_ops,
    }


@pytest.mark.anyio
async def test_invoke_once_returns_none_result_as_success(mocker):
    """Actions declared `-> None` must round-trip as a real result.

    If the success path ever treats `None` as "no result" (e.g. a sentinel
    check that regresses to `is None`), a successful `core.cases.delete_case`
    is reported as a failure and retried after its side effect landed.
    """
    role = _expression_policy_role("tracecat-executor")
    action_input = _expression_policy_input("core.cases.delete_case", {})
    resolved_context = mocker.Mock(logical_time=mocker.sentinel.logical_time)
    prepared_context = executor_service.PreparedContext(
        resolved_context=resolved_context,
        mask_values={"secret"},
    )

    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    mocker.patch.object(
        executor_service,
        "_invoke_step",
        new=mocker.AsyncMock(return_value=None),
    )

    result = await executor_service.invoke_once(
        backend=mocker.Mock(),
        input=action_input,
        ctx=executor_service.DispatchActionContext(role=role),
    )

    assert result is None


@pytest.mark.anyio
async def test_invoke_once_withholds_carrier_derived_action_error(mocker):
    """No declared secrets does not mean no secrets.

    ACTIONS/var inputs can be secret-derived, so the original exception must
    not ride along as __cause__/__context__: Temporal serializes the chain and
    the run view surfaces its deepest message.
    """
    from tracecat.exceptions import ExecutionError

    canary = "SUPERSECRET-chain-canary"
    role = _expression_policy_role("tracecat-executor")
    action_input = _expression_policy_input(
        "core.probe", {"value": "${{ ACTIONS.fetch.result }}"}
    )
    resolved_context = mocker.Mock(logical_time=mocker.sentinel.logical_time)
    prepared_context = executor_service.PreparedContext(
        resolved_context=resolved_context,
        mask_values=set(),
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    action_error = ExecutionError(
        info=ExecutorActionErrorInfo(
            action_name="core.probe",
            type="ValueError",
            message=f"rejected {canary}",
            filename="probe.py",
            function="run",
        )
    )
    mocker.patch.object(
        executor_service,
        "_invoke_step",
        new=mocker.AsyncMock(side_effect=action_error),
    )

    with pytest.raises(ExecutionError) as exc_info:
        await executor_service.invoke_once(
            backend=mocker.Mock(),
            input=action_input,
            ctx=executor_service.DispatchActionContext(role=role),
        )

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert "Details withheld:" in str(exc_info.value)
    assert canary not in str(exc_info.value)


@pytest.mark.anyio
async def test_invoke_once_keeps_action_error_when_withholding_disabled(
    mocker, monkeypatch
):
    """Opting out preserves diagnostics while masking known values in all fields."""
    canary = "secret-error-info-canary"
    monkeypatch.setattr(
        config, "TRACECAT__UNSAFE_DISABLE_SECRET_ERROR_WITHHOLDING", True
    )
    role = _expression_policy_role("tracecat-executor")
    action_input = _expression_policy_input(
        "core.probe", {"value": "${{ ACTIONS.fetch.result }}"}
    )
    resolved_context = mocker.Mock(logical_time=mocker.sentinel.logical_time)
    prepared_context = executor_service.PreparedContext(
        resolved_context=resolved_context,
        mask_values={canary},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    action_error = ExecutionError(
        info=ExecutorActionErrorInfo(
            action_name=f"core.{canary}",
            type=f"Error_{canary}",
            message=f"upstream rejected the request: {canary}",
            filename=f"{canary}.py",
            function=f"run_{canary}",
            lineno=42,
            loop_vars={"value": [canary]},
        )
    )
    original_info = action_error.info.model_dump()
    mocker.patch.object(
        executor_service,
        "_invoke_step",
        new=mocker.AsyncMock(side_effect=action_error),
    )

    with pytest.raises(ExecutionError) as exc_info:
        await executor_service.invoke_once(
            backend=mocker.Mock(),
            input=action_input,
            ctx=executor_service.DispatchActionContext(role=role),
            iteration=2,
        )

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "Details withheld:" not in str(error)
    assert "upstream rejected the request" in str(error)
    assert canary not in str(error)
    assert canary not in error.info.model_dump_json()
    assert MASK_VALUE in error.info.message
    assert error.info.lineno == 42
    assert error.info.loop_iteration == 2
    assert error.info.loop_vars == {"value": [MASK_VALUE]}
    assert action_error.info.model_dump() == original_info


def _patch_org_error_details_setting(mocker, value: object):
    """Stub the raw org setting row behind `workspace_allows_error_details`.

    `value` is what the stored allow-list deserializes to (a list of workspace
    ID strings); `None` mimics a missing row.
    """
    executor_service._workspace_allows_error_details_cached.cache_clear()
    session_cm = mocker.MagicMock()
    session_cm.__aenter__ = mocker.AsyncMock(return_value=mocker.AsyncMock())
    session_cm.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch.object(
        executor_service,
        "get_async_session_bypass_rls_context_manager",
        return_value=session_cm,
    )
    stub = mocker.AsyncMock(return_value=[] if value is None else value)
    return mocker.patch(
        "tracecat.settings.service.get_setting_from_bypass_session", new=stub
    )


_CURRENT_WS = str(UUID(int=2))
_OTHER_WS = str(UUID(int=3))


@pytest.mark.anyio
async def test_workspace_allows_error_details_lookup_is_cached(mocker) -> None:
    """Repeated checks for the same org/workspace hit the DB once within the TTL."""
    stub = _patch_org_error_details_setting(mocker, [_CURRENT_WS])
    role = Role(
        type="service",
        service_id="tracecat-executor",
        organization_id=UUID(int=1),
        workspace_id=UUID(int=2),
    )
    other = role.model_copy(update={"workspace_id": UUID(int=3)})

    assert await executor_service._workspace_allows_error_details(role) is True
    assert await executor_service._workspace_allows_error_details(role) is True
    assert stub.await_count == 1

    assert await executor_service._workspace_allows_error_details(other) is False
    assert stub.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("allowed_workspaces", "action_opts_in", "expect_original"),
    [
        pytest.param([_CURRENT_WS], True, True, id="workspace-allowed-and-action"),
        pytest.param(
            [_OTHER_WS, _CURRENT_WS], True, True, id="workspace-among-allowed"
        ),
        pytest.param([_CURRENT_WS], False, False, id="workspace-allowed-only"),
        pytest.param([_OTHER_WS], True, False, id="other-workspace-allowed"),
        pytest.param([], True, False, id="allow-list-empty"),
        pytest.param(None, True, False, id="allow-list-missing"),
        pytest.param("not-a-list", True, False, id="allow-list-malformed"),
    ],
)
async def test_invoke_once_action_opt_out_requires_workspace_allow(
    mocker, monkeypatch, allowed_workspaces, action_opts_in, expect_original
):
    """The per-action opt-out only surfaces the message for org-allow-listed workspaces."""
    from tracecat.exceptions import ExecutionError

    monkeypatch.setattr(
        config, "TRACECAT__UNSAFE_DISABLE_SECRET_ERROR_WITHHOLDING", False
    )
    role = _expression_policy_role("tracecat-executor")
    action_input = _expression_policy_input(
        "core.probe", {"value": "${{ ACTIONS.fetch.result }}"}
    )
    action_input.task.unsafe_disable_secret_error_withholding = action_opts_in
    get_setting = _patch_org_error_details_setting(mocker, allowed_workspaces)
    resolved_context = mocker.Mock(logical_time=mocker.sentinel.logical_time)
    prepared_context = executor_service.PreparedContext(
        resolved_context=resolved_context,
        mask_values={"sk-live-secret"},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    action_error = ExecutionError(
        info=ExecutorActionErrorInfo(
            action_name="core.probe",
            type="ValueError",
            message="upstream rejected the request",
            filename="probe.py",
            function="run",
        )
    )
    mocker.patch.object(
        executor_service,
        "_invoke_step",
        new=mocker.AsyncMock(side_effect=action_error),
    )

    with pytest.raises(ExecutionError) as exc_info:
        await executor_service.invoke_once(
            backend=mocker.Mock(),
            input=action_input,
            ctx=executor_service.DispatchActionContext(role=role),
        )

    if expect_original:
        assert "Details withheld:" not in str(exc_info.value)
        assert "upstream rejected the request" in str(exc_info.value)
    else:
        assert "Details withheld:" in str(exc_info.value)
        assert "upstream rejected the request" not in str(exc_info.value)
    # The per-invocation policy never leaks past invoke_once.
    assert ctx_unsafe_disable_secret_error_withholding.get() is False
    if action_opts_in:
        assert get_setting.await_args.args == (
            "app_unsafe_disable_secret_error_withholding_workspace_ids",
        )
        assert get_setting.await_args.kwargs["organization_id"] == role.organization_id
    else:
        get_setting.assert_not_awaited()


@pytest.mark.anyio
async def test_invoke_once_action_opt_out_fails_closed_without_workspace(
    mocker, monkeypatch
):
    """No workspace on the role means the allow-list is never consulted."""
    from tracecat.exceptions import ExecutionError

    monkeypatch.setattr(
        config, "TRACECAT__UNSAFE_DISABLE_SECRET_ERROR_WITHHOLDING", False
    )
    role = Role(
        type="service",
        organization_id=UUID(int=1),
        workspace_id=None,
        service_id="tracecat-executor",
    )
    action_input = _expression_policy_input(
        "core.probe", {"value": "${{ ACTIONS.fetch.result }}"}
    )
    action_input.task.unsafe_disable_secret_error_withholding = True
    get_setting = _patch_org_error_details_setting(mocker, [_CURRENT_WS])
    prepared_context = executor_service.PreparedContext(
        resolved_context=mocker.Mock(logical_time=mocker.sentinel.logical_time),
        mask_values={"sk-live-secret"},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    mocker.patch.object(
        executor_service,
        "_invoke_step",
        new=mocker.AsyncMock(
            side_effect=ExecutionError(
                info=ExecutorActionErrorInfo(
                    action_name="core.probe",
                    type="ValueError",
                    message="upstream rejected the request",
                    filename="probe.py",
                    function="run",
                )
            )
        ),
    )

    with pytest.raises(ExecutionError) as exc_info:
        await executor_service.invoke_once(
            backend=mocker.Mock(),
            input=action_input,
            ctx=executor_service.DispatchActionContext(role=role),
        )

    assert "Details withheld:" in str(exc_info.value)
    assert "upstream rejected the request" not in str(exc_info.value)
    get_setting.assert_not_awaited()
    assert ctx_unsafe_disable_secret_error_withholding.get() is False


@pytest.mark.anyio
@pytest.mark.parametrize("failure_site", ["step", "returns", "nested-returns"])
async def test_invoke_once_opt_out_masks_template_expression_errors(
    mocker, monkeypatch, failure_site
):
    """Real template failures retain diagnostics, but never known secret values."""
    monkeypatch.setattr(
        config, "TRACECAT__UNSAFE_DISABLE_SECRET_ERROR_WITHHOLDING", False
    )
    canary = "secret-template-canary"
    expression = "${{ int(SECRETS.api.KEY) }}"
    role = _expression_policy_role("tracecat-executor")
    action_input = _expression_policy_input("testing.error_details", {})
    action_input.task.unsafe_disable_secret_error_withholding = True
    _patch_org_error_details_setting(mocker, [_CURRENT_WS])
    template_definition = {
        "name": "error_details",
        "namespace": "testing",
        "title": "Error details",
        "description": "Exercises secret-bearing expression failures",
        "display_group": "Testing",
        "expects": {},
        "steps": [
            {
                "ref": "probe",
                "action": "core.probe",
                "args": {"value": expression} if failure_site == "step" else {},
            }
        ],
        "returns": expression,
    }
    action_impl = ActionImplementation(
        type="template",
        action_name="testing.error_details",
        template_definition=template_definition,
    )
    if failure_site == "nested-returns":
        # The nested failure is wrapped in ExecutionError before reaching the
        # root; the other cases reach invoke_once as ordinary exceptions.
        action_impl = action_impl.model_copy(
            update={
                "template_definition": {
                    **template_definition,
                    "steps": [{"ref": "nested", "action": "testing.inner", "args": {}}],
                }
            }
        )
    resolved_context = ResolvedContext(
        secrets={"api": {"KEY": canary}},
        action_impl=action_impl,
        evaluated_args={},
        workspace_id=str(role.workspace_id),
        workflow_id=str(action_input.run_context.wf_id),
        run_id=str(action_input.run_context.wf_run_id),
        executor_token="parent-token",
    )
    prepared_context = executor_service.PreparedContext(
        resolved_context=resolved_context,
        mask_values={canary},
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service,
        "prepare_resolved_context",
        new=mocker.AsyncMock(return_value=prepared_context),
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "collect_action_secrets_from_manifest",
        new=mocker.AsyncMock(return_value=[]),
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            side_effect=(
                [
                    ActionImplementation(
                        type="template",
                        action_name="testing.inner",
                        template_definition=template_definition,
                    ),
                    ActionImplementation(type="udf", action_name="core.probe"),
                ]
                if failure_site == "nested-returns"
                else [ActionImplementation(type="udf", action_name="core.probe")]
            )
        ),
    )
    mocker.patch.object(
        executor_service, "_mint_action_executor_token", return_value="step-token"
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(return_value=ExecutorResultSuccess(result=None))
    error_log = mocker.patch.object(executor_service.logger, "error")

    with pytest.raises(ExecutionError) as exc_info:
        await executor_service.invoke_once(
            backend=backend,
            input=action_input,
            ctx=executor_service.DispatchActionContext(role=role),
            iteration=2,
        )

    error = exc_info.value
    assert canary not in error.info.model_dump_json()
    assert canary not in str(error)
    assert "invalid literal for int()" in error.info.message
    assert MASK_VALUE in error.info.message
    assert "Details withheld" not in error.info.message
    assert error.info.type == "TracecatExpressionError"
    assert error.info.loop_iteration == 2
    assert error.__cause__ is None
    assert error.__context__ is None
    assert ctx_unsafe_disable_secret_error_withholding.get() is False
    assert canary not in str(error_log.call_args_list)
    error_log.assert_called()
    if failure_site != "nested-returns":
        assert error_log.call_args.kwargs["error"] == error.info.message
    assert backend.execute.await_count == (0 if failure_site == "step" else 1)


@pytest.mark.anyio
async def test_template_expects_validation_leaves_no_plaintext_in_chain(mocker):
    """The sanitized message is only clean if nothing plaintext is chained."""
    from tracecat.exceptions import RegistryValidationError

    canary = "SUPERSECRET-expects-canary"
    action_input = _expression_policy_input("testing.typed", {"count": canary})
    role = _expression_policy_role("tracecat-executor")
    resolved = ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(
            type="template",
            action_name="testing.typed",
            template_definition={
                "name": "typed",
                "namespace": "testing",
                "title": "Typed",
                "description": "Rejects non-int input",
                "display_group": "Testing",
                "expects": {"count": {"type": "int", "description": "n"}},
                "steps": [
                    {"ref": "noop", "action": "core.noop", "args": {}},
                ],
                "returns": "${{ inputs.count }}",
            },
        ),
        evaluated_args={"count": canary},
        workspace_id=str(role.workspace_id),
        workflow_id=str(action_input.run_context.wf_id),
        run_id=str(action_input.run_context.wf_run_id),
        executor_token="parent-token",
    )

    with pytest.raises(RegistryValidationError) as exc_info:
        await executor_service._execute_template_action(
            backend=mocker.Mock(),
            input=action_input,
            ctx=executor_service.DispatchActionContext(role=role),
            resolved_context=resolved,
            timeout=30,
            provenance=_policy_source_provenance({"count": canary}),
        )

    err = exc_info.value
    assert err.__cause__ is None
    assert err.__context__ is None
    assert canary not in str(err)


@pytest.mark.anyio
@pytest.mark.parametrize("use_nsjail", [False, True])
async def test_invoke_once_sandbox_failure_is_secret_safe(
    tmp_path, mocker, use_nsjail: bool
) -> None:
    """Resolve a secret, run the real sandbox service, and keep failure safe."""
    action_name = "core.script.run_python"
    secret = "synthetic-sandbox-secret"
    derived_secret = "terces-xobdnas-citehtnys"
    stdout = f"stdout={secret}; derived={derived_secret}"
    stderr = f"stderr={secret}; derived={derived_secret}"
    result = SandboxResult(
        success=False,
        error=f"ValueError: {secret}",
        stdout=stdout,
        stderr=stderr,
        error_code=SandboxErrorCode.WORKLOAD_FAILURE,
        exit_code=1,
        execution_time_ms=12.5,
    )
    sandbox_service = SandboxService(cache_dir=str(tmp_path / "sandbox-cache"))
    sandbox_executor = mocker.Mock()
    sandbox_executor.execute = mocker.AsyncMock(return_value=result)
    mocker.patch.object(
        sandbox_service, "_is_nsjail_available", return_value=use_nsjail
    )
    if use_nsjail:
        sandbox_service._nsjail_executor = cast(Any, sandbox_executor)
    else:
        sandbox_service._unsafe_pid_executor = cast(Any, sandbox_executor)

    class SandboxBackend:
        async def execute(
            self,
            *,
            input: RunActionInput,
            role: Role,
            resolved_context: ResolvedContext,
            timeout: float,
        ) -> ExecutorResultSuccess:
            del input, role, timeout
            args = resolved_context.evaluated_args
            assert args["inputs"] == {"value": secret}
            assert args["env_vars"] == {"USER_SECRET": secret}
            output = await sandbox_service.run_python(
                script=args["script"],
                inputs=args.get("inputs"),
                dependencies=args.get("dependencies"),
                timeout_seconds=args.get("timeout_seconds"),
                allow_network=args.get("allow_network", False),
                env_vars=args.get("env_vars"),
                python_path_dirs=[],
                workspace_id=resolved_context.workspace_id,
            )
            return ExecutorResultSuccess(result=output)

    mocker.patch.object(
        executor_service.registry_resolver,
        "prefetch_lock",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            return_value=ActionImplementation(type="udf", action_name=action_name)
        ),
    )
    mocker.patch.object(
        executor_service.registry_resolver,
        "collect_action_secrets_from_manifest",
        new=mocker.AsyncMock(return_value=set()),
    )
    get_action_secrets = mocker.patch.object(
        executor_service.secrets_manager,
        "get_action_secrets",
        new=mocker.AsyncMock(return_value={"runtime": {"TOKEN": secret}}),
    )
    mocker.patch.object(
        executor_service,
        "get_workspace_variables",
        new=mocker.AsyncMock(return_value={}),
    )
    mocker.patch.object(
        executor_service,
        "project_secret_env",
        new=mocker.AsyncMock(
            return_value=SecretEnvProjection(env={}, mask_values=set())
        ),
    )
    mocker.patch.object(
        executor_service,
        "_mint_action_executor_token",
        return_value="synthetic-executor-token",
    )
    mocker.patch.object(
        executor_service.config,
        "TRACECAT__UNSAFE_DISABLE_SM_MASKING",
        False,
    )

    action_input = _expression_policy_input(
        action_name,
        {
            "script": "def main(value):\n    raise ValueError(value)\n",
            "inputs": {"value": "${{ SECRETS.runtime.TOKEN }}"},
            "env_vars": {"USER_SECRET": "${{ SECRETS.runtime.TOKEN }}"},
        },
    )
    role = _expression_policy_role("tracecat-executor")
    capture_activity_failure = mocker.spy(executor_service, "capture_activity_failure")
    capture_exception = mocker.patch.object(sentry_sdk, "capture_exception")
    records: list[Any] = []

    def collect_record(message: Any) -> None:
        records.append(message.record)

    sink_id = executor_service.logger.add(collect_record, level="TRACE")
    try:
        with pytest.raises(ExecutionError) as exc_info:
            await executor_service.invoke_once(
                cast(Any, SandboxBackend()),
                action_input,
                executor_service.DispatchActionContext(role),
            )
    finally:
        executor_service.logger.remove(sink_id)

    error = exc_info.value
    get_action_secrets.assert_awaited_once()
    assert get_action_secrets.await_args.kwargs["secret_exprs"] == {"runtime.TOKEN"}
    assert error.info.message == (
        "The action failed. Details withheld: secrets may be in scope."
    )
    assert secret not in str(error)
    assert derived_secret not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.sentry_capture is None

    classification = classify_execute_action_error(error, action_name=action_name)
    assert classification.kind is RuntimeErrorKind.ACTION_EXECUTION_FAILED
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert secret not in classification.model_dump_json()
    assert derived_secret not in classification.model_dump_json()
    capture_activity_failure.assert_called_once()
    assert capture_activity_failure.spy_return is None
    capture_exception.assert_not_called()

    rendered_records = "\n".join(
        f"{record['message']} {record['extra']!r}" for record in records
    )
    assert secret not in rendered_records
    assert derived_secret not in rendered_records
    failure_records = [
        record
        for record in records
        if record["message"]
        in {
            "Script execution failed",
            "Script execution failed (unsafe PID executor)",
        }
    ]
    assert len(failure_records) == 1
    failure_extra = failure_records[0]["extra"]
    assert failure_extra["error_code"] is SandboxErrorCode.WORKLOAD_FAILURE
    assert failure_extra["exit_code"] == 1
    assert failure_extra["stdout_chars"] == len(stdout)
    assert failure_extra["stderr_chars"] == len(stderr)
