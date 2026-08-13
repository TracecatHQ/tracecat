import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretType,
)

from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import TracecatCredentialsError
from tracecat.executor import service as executor_service
from tracecat.executor.schemas import (
    ActionImplementation,
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
from tracecat.secrets import secrets_manager
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
        runtime_parameter: f"{MASK_VALUE}:runtime-variable",
        preserved_parameter: preserved_source,
    }
    assert get_action_secrets.await_args.kwargs == {
        "secret_exprs": set(),
        "action_secrets": action_secrets,
    }
    assert get_workspace_variables.await_args.kwargs["variable_exprs"] == {"runtime"}
    assert prepared.mask_values == {"runtime-secret"}


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
        "case_id": MASK_VALUE,
        "content": (
            f"Host: api.example.com, token: {MASK_VALUE}, encoded: {MASK_VALUE}"
        ),
    }
    assert get_action_secrets.await_args.kwargs == {
        "secret_exprs": set(),
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
async def test_prepare_resolved_context_redacts_unmapped_parameters(
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

    assert prepared.resolved_context.evaluated_args == {parameter: MASK_VALUE}
    assert get_action_secrets.await_args.kwargs["secret_exprs"] == set()
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
                "expects": {},
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
