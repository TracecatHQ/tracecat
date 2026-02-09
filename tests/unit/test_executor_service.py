"""Tests for tracecat/executor/service.py.

Tests the executor service layer: expression evaluation, context resolution,
action dispatch, for_each loops, secret masking, and utility functions.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretType,
)

from tracecat.auth.types import AccessLevel, OrgRole, Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import (
    ActionStatement,
    DSLEnvironment,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.exceptions import (
    ExecutionError,
    TracecatCredentialsError,
)
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.service import (
    DispatchActionContext,
    PreparedContext,
    dispatch_action,
    evaluate_templated_args,
    flatten_wrapped_exc_error_group,
    invoke_once,
    iter_for_each,
    patch_object,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import OAuthGrantType
from tracecat.registry.lock.types import RegistryLock
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import apply_masks_object


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


# --- Helpers ---


def _make_materialized_context(
    *,
    actions: dict[str, dict[str, Any]] | None = None,
    trigger: Any = None,
    env: DSLEnvironment | None = None,
    secrets: dict[str, Any] | None = None,
    local_vars: dict[str, Any] | None = None,
) -> ExecutionContext:
    """Create a materialized ExecutionContext for testing expression evaluation.

    In production, `materialize_context()` converts StoredObject wrappers
    (InlineObject, ExternalObject) to plain dicts before expression evaluation.
    This helper builds contexts in that materialized form so jsonpath_ng can
    traverse them.
    """
    ctx = cast(
        ExecutionContext,
        {
            "ACTIONS": actions or {},
            "TRIGGER": trigger,
            "ENV": env or DSLEnvironment(),
        },
    )
    if secrets is not None:
        ctx["SECRETS"] = secrets
    if local_vars is not None:
        ctx["var"] = local_vars
    return ctx


def _make_role() -> Role:
    """Create a test Role."""
    return Role(
        type="user",
        workspace_id=uuid4(),
        organization_id=uuid4(),
        user_id=uuid4(),
        service_id="tracecat-api",
        access_level=AccessLevel.BASIC,
        org_role=OrgRole.MEMBER,
    )


def _make_action_statement(
    ref: str = "test_action",
    action: str = "core.transform.reshape",
    args: Mapping[str, Any] | None = None,
    for_each: str | list[str] | None = None,
) -> ActionStatement:
    """Create a test ActionStatement."""
    return ActionStatement(
        ref=ref,
        action=action,
        args=args if args is not None else {"value": "hello"},
        for_each=for_each,
    )


def _make_registry_lock() -> RegistryLock:
    return RegistryLock(
        origins={"tracecat_registry": "2024.12.10.000000"},
        actions={"core.transform.reshape": "tracecat_registry"},
    )


def _make_run_context() -> RunContext:
    wf_id = WorkflowUUID(int=1)
    return RunContext(
        wf_id=wf_id,
        wf_exec_id=f"{wf_id.short()}:exec_test",
        wf_run_id=uuid4(),
        environment="default",
        logical_time=datetime(2024, 1, 1, 12, 0, 0),
    )


def _make_run_action_input(
    task: ActionStatement | None = None,
    exec_context: ExecutionContext | None = None,
) -> RunActionInput:
    """Create a test RunActionInput.

    When a materialized exec_context (plain dicts) is provided, uses
    model_construct() to skip Pydantic validation — matching production
    where materialize_context() runs before dispatch_action().
    """
    if exec_context is not None:
        # Materialized contexts have plain dicts that won't pass
        # StoredObject discriminated union validation, so skip it.
        return RunActionInput.model_construct(
            task=task or _make_action_statement(),
            exec_context=exec_context,
            run_context=_make_run_context(),
            registry_lock=_make_registry_lock(),
        )
    return RunActionInput(
        task=task or _make_action_statement(),
        exec_context=create_default_execution_context(),
        run_context=_make_run_context(),
        registry_lock=_make_registry_lock(),
    )


def _make_resolved_context(
    action_type: str = "udf",
    evaluated_args: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
    logical_time: datetime | None = None,
) -> ResolvedContext:
    return ResolvedContext(
        secrets=secrets or {},
        variables={},
        action_impl=ActionImplementation(
            type=action_type,
            action_name="core.transform.reshape",
            module="tracecat_registry.integrations.core.transform",
            name="reshape",
        ),
        evaluated_args=evaluated_args or {"value": "hello"},
        workspace_id=str(uuid4()),
        workflow_id=str(uuid4()),
        run_id=str(uuid4()),
        executor_token="test-token",
        logical_time=logical_time,
    )


# --- Test Classes ---


@pytest.mark.anyio
class TestEvaluateTemplatedArgs:
    """Test expression evaluation with various context types."""

    async def test_plain_args_passthrough(self) -> None:
        """Plain args without expressions should be returned as-is."""
        task = _make_action_statement(args={"value": "hello", "count": 42})
        context = _make_materialized_context()
        result = evaluate_templated_args(task, context)
        assert result["value"] == "hello"
        assert result["count"] == 42

    async def test_trigger_context_expression(self) -> None:
        """TRIGGER expressions should resolve from trigger data."""
        task = _make_action_statement(args={"value": "${{ TRIGGER.name }}"})
        context = _make_materialized_context(trigger={"name": "test_trigger"})
        result = evaluate_templated_args(task, context)
        assert result["value"] == "test_trigger"

    async def test_actions_context_expression(self) -> None:
        """ACTIONS expressions should resolve from previous action results."""
        task = _make_action_statement(args={"value": "${{ ACTIONS.step_a.result }}"})
        context = _make_materialized_context(
            actions={"step_a": {"result": "step_a_output", "result_typename": "str"}}
        )
        result = evaluate_templated_args(task, context)
        assert result["value"] == "step_a_output"

    async def test_env_context_expression(self) -> None:
        """ENV expressions should resolve from environment data."""
        task = _make_action_statement(args={"env_val": "${{ ENV.workflow.wf_id }}"})
        context = _make_materialized_context(
            env=DSLEnvironment(workflow={"wf_id": "wf-123"})
        )
        result = evaluate_templated_args(task, context)
        assert result["env_val"] == "wf-123"

    async def test_secrets_context_expression(self) -> None:
        """SECRETS expressions should resolve from secrets data."""
        task = _make_action_statement(
            args={"api_key": "${{ SECRETS.my_secret.API_KEY }}"}
        )
        context = _make_materialized_context(
            secrets={"my_secret": {"API_KEY": "sk-12345"}}
        )
        result = evaluate_templated_args(task, context)
        assert result["api_key"] == "sk-12345"

    async def test_nested_expressions(self) -> None:
        """Nested dict/list args with expressions should resolve correctly."""
        task = _make_action_statement(
            args={
                "nested": {
                    "inner": "${{ TRIGGER.name }}",
                    "list_val": ["${{ TRIGGER.items }}"],
                }
            }
        )
        context = _make_materialized_context(
            trigger={"name": "test", "items": [1, 2, 3]}
        )
        result = evaluate_templated_args(task, context)
        assert result["nested"]["inner"] == "test"
        assert result["nested"]["list_val"] == [[1, 2, 3]]

    async def test_mixed_template_and_literal(self) -> None:
        """String interpolation mixing template and literal text."""
        task = _make_action_statement(args={"msg": "Hello ${{ TRIGGER.name }}!"})
        context = _make_materialized_context(trigger={"name": "world"})
        result = evaluate_templated_args(task, context)
        assert result["msg"] == "Hello world!"

    async def test_local_vars_expression(self) -> None:
        """var (LOCAL_VARS) expressions should resolve from for_each context."""
        task = _make_action_statement(args={"item": "${{ var.item }}"})
        context = _make_materialized_context(local_vars={"item": "loop_value"})
        result = evaluate_templated_args(task, context)
        assert result["item"] == "loop_value"

    async def test_empty_args(self) -> None:
        """Empty args should return empty mapping."""
        task = _make_action_statement(args={})
        context = _make_materialized_context()
        result = evaluate_templated_args(task, context)
        assert dict(result) == {}


class TestPatchObject:
    """Test the patch_object utility for nested dict mutation."""

    def test_single_level_patch(self) -> None:
        obj: dict[str, Any] = {}
        patch_object(obj, path="key", value="val")
        assert obj == {"key": "val"}

    def test_nested_patch(self) -> None:
        obj: dict[str, Any] = {}
        patch_object(obj, path="a.b.c", value=42)
        assert obj == {"a": {"b": {"c": 42}}}

    def test_overwrite_existing(self) -> None:
        obj: dict[str, Any] = {"a": {"b": "old"}}
        patch_object(obj, path="a.b", value="new")
        assert obj["a"]["b"] == "new"

    def test_custom_separator(self) -> None:
        obj: dict[str, Any] = {}
        patch_object(obj, path="a/b/c", value="val", sep="/")
        assert obj == {"a": {"b": {"c": "val"}}}

    def test_patch_preserves_siblings(self) -> None:
        obj: dict[str, Any] = {"a": {"existing": 1}}
        patch_object(obj, path="a.new_key", value=2)
        assert obj == {"a": {"existing": 1, "new_key": 2}}


@pytest.mark.anyio
class TestIterForEach:
    """Test loop iteration helper with single/multiple iterables."""

    async def test_single_iterable(self) -> None:
        """Single for_each expression should yield patched args per item."""
        task = _make_action_statement(
            args={"value": "${{ var.item }}"},
            for_each="${{ for var.item in TRIGGER.items }}",
        )
        context = _make_materialized_context(trigger={"items": ["a", "b", "c"]})
        results = list(iter_for_each(task, context))
        assert len(results) == 3
        assert results[0]["value"] == "a"
        assert results[1]["value"] == "b"
        assert results[2]["value"] == "c"

    async def test_multiple_iterables(self) -> None:
        """Multiple for_each expressions should zip iterables together."""
        task = _make_action_statement(
            args={"k": "${{ var.key }}", "v": "${{ var.val }}"},
            for_each=[
                "${{ for var.key in TRIGGER.keys }}",
                "${{ for var.val in TRIGGER.vals }}",
            ],
        )
        context = _make_materialized_context(
            trigger={"keys": ["k1", "k2"], "vals": ["v1", "v2"]}
        )
        results = list(iter_for_each(task, context))
        assert len(results) == 2
        assert results[0] == {"k": "k1", "v": "v1"}
        assert results[1] == {"k": "k2", "v": "v2"}

    async def test_no_for_each_raises(self) -> None:
        """Should raise ValueError if no for_each is configured."""
        task = _make_action_statement(for_each=None)
        context = _make_materialized_context()
        with pytest.raises(ValueError, match="No loop expression found"):
            list(iter_for_each(task, context))

    async def test_empty_iterable(self) -> None:
        """Empty iterable should yield no results."""
        task = _make_action_statement(
            args={"value": "${{ var.item }}"},
            for_each="${{ for var.item in TRIGGER.items }}",
        )
        context = _make_materialized_context(trigger={"items": []})
        results = list(iter_for_each(task, context))
        assert results == []


class TestFlattenWrappedExcErrorGroup:
    """Test exception group flattening utility."""

    def test_single_exception(self) -> None:
        exc = ExecutionError(
            info=ExecutorActionErrorInfo(
                action_name="test",
                type="ValueError",
                message="boom",
                filename="test.py",
                function="test_fn",
            )
        )
        result = flatten_wrapped_exc_error_group(exc)
        assert len(result) == 1
        assert result[0] is exc

    def test_exception_group(self) -> None:
        exc1 = ExecutionError(
            info=ExecutorActionErrorInfo(
                action_name="a",
                type="ValueError",
                message="err1",
                filename="test.py",
                function="fn",
            )
        )
        exc2 = ExecutionError(
            info=ExecutorActionErrorInfo(
                action_name="b",
                type="TypeError",
                message="err2",
                filename="test.py",
                function="fn",
            )
        )
        eg = ExceptionGroup("test", [exc1, exc2])
        result = flatten_wrapped_exc_error_group(eg)
        assert len(result) == 2
        assert result[0] is exc1
        assert result[1] is exc2

    def test_nested_exception_group(self) -> None:
        exc1 = ExecutionError(
            info=ExecutorActionErrorInfo(
                action_name="a",
                type="ValueError",
                message="err1",
                filename="test.py",
                function="fn",
            )
        )
        exc2 = ExecutionError(
            info=ExecutorActionErrorInfo(
                action_name="b",
                type="TypeError",
                message="err2",
                filename="test.py",
                function="fn",
            )
        )
        inner_eg = ExceptionGroup("inner", [exc2])
        outer_eg = cast(
            BaseExceptionGroup[ExecutionError],
            ExceptionGroup("outer", [exc1, inner_eg]),
        )
        result = flatten_wrapped_exc_error_group(outer_eg)
        assert len(result) == 2
        assert result[0] is exc1
        assert result[1] is exc2


class TestSecretMasking:
    """Test post-execution secret masking via apply_masks_object."""

    def test_mask_string(self) -> None:
        result = apply_masks_object("my-secret-value", masks={"my-secret-value"})
        assert result == "***"

    def test_mask_in_dict(self) -> None:
        data = {"key": "my-secret-value", "safe": "ok"}
        result = apply_masks_object(data, masks={"my-secret-value"})
        assert result["key"] == "***"
        assert result["safe"] == "ok"

    def test_mask_in_list(self) -> None:
        data = ["my-secret-value", "safe"]
        result = apply_masks_object(data, masks={"my-secret-value"})
        assert result[0] == "***"
        assert result[1] == "safe"

    def test_mask_nested(self) -> None:
        data = {"outer": {"inner": "secret123"}}
        result = apply_masks_object(data, masks={"secret123"})
        assert result["outer"]["inner"] == "***"

    def test_no_mask_when_not_matching(self) -> None:
        data = {"key": "safe_value"}
        result = apply_masks_object(data, masks={"other_secret"})
        assert result["key"] == "safe_value"

    def test_mask_partial_string(self) -> None:
        """Secrets embedded in larger strings should be masked."""
        result = apply_masks_object("Bearer my-secret-token", masks={"my-secret-token"})
        assert "my-secret-token" not in result
        assert "Bearer" in result

    def test_non_string_passthrough(self) -> None:
        """Non-string values should pass through unmasked."""
        assert apply_masks_object(42, masks={"42"}) == 42
        assert apply_masks_object(True, masks={"True"}) is True
        assert apply_masks_object(None, masks={"None"}) is None


@pytest.mark.anyio
class TestInvokeOnce:
    """Test single action dispatch through invoke_once()."""

    async def test_successful_udf_execution(self) -> None:
        """invoke_once should return result from successful UDF execution."""
        backend = AsyncMock()
        backend.execute.return_value = ExecutorResultSuccess(result={"output": 42})

        input_ = _make_run_action_input()
        role = _make_role()
        ctx = DispatchActionContext(role=role)

        resolved = _make_resolved_context(evaluated_args={"value": "hello"})
        prepared = PreparedContext(resolved_context=resolved, mask_values=None)

        with (
            patch(
                "tracecat.executor.service.registry_resolver.prefetch_lock",
                new_callable=AsyncMock,
            ),
            patch(
                "tracecat.executor.service.prepare_resolved_context",
                new_callable=AsyncMock,
                return_value=prepared,
            ),
        ):
            result = await invoke_once(backend, input_, ctx)

        assert result == {"output": 42}

    async def test_execution_error_adds_loop_context(self) -> None:
        """ExecutionError should have loop_iteration set when iteration provided."""
        backend = AsyncMock()
        error_info = ExecutorActionErrorInfo(
            action_name="test",
            type="ValueError",
            message="boom",
            filename="test.py",
            function="fn",
        )

        resolved = _make_resolved_context()
        prepared = PreparedContext(resolved_context=resolved, mask_values=None)

        with (
            patch(
                "tracecat.executor.service.registry_resolver.prefetch_lock",
                new_callable=AsyncMock,
            ),
            patch(
                "tracecat.executor.service.prepare_resolved_context",
                new_callable=AsyncMock,
                return_value=prepared,
            ),
            patch(
                "tracecat.executor.service._invoke_step",
                new_callable=AsyncMock,
                side_effect=ExecutionError(info=error_info),
            ),
        ):
            input_ = _make_run_action_input()
            ctx = DispatchActionContext(role=_make_role())
            with pytest.raises(ExecutionError) as exc_info:
                await invoke_once(backend, input_, ctx, iteration=5)

            assert exc_info.value.info is not None
            assert exc_info.value.info.loop_iteration == 5

    async def test_secret_masking_applied(self) -> None:
        """Secrets should be masked in the result when mask_values is set."""
        backend = AsyncMock()
        backend.execute.return_value = ExecutorResultSuccess(
            result={"output": "contains my-secret-key here"}
        )

        resolved = _make_resolved_context()
        prepared = PreparedContext(
            resolved_context=resolved,
            mask_values={"my-secret-key"},
        )

        with (
            patch(
                "tracecat.executor.service.registry_resolver.prefetch_lock",
                new_callable=AsyncMock,
            ),
            patch(
                "tracecat.executor.service.prepare_resolved_context",
                new_callable=AsyncMock,
                return_value=prepared,
            ),
        ):
            input_ = _make_run_action_input()
            ctx = DispatchActionContext(role=_make_role())
            result = await invoke_once(backend, input_, ctx)

        assert "my-secret-key" not in str(result)

    async def test_infrastructure_error_wrapped(self) -> None:
        """Non-ExecutionError exceptions should be wrapped in ExecutionError."""
        backend = AsyncMock()
        resolved = _make_resolved_context()
        prepared = PreparedContext(resolved_context=resolved, mask_values=None)

        with (
            patch(
                "tracecat.executor.service.registry_resolver.prefetch_lock",
                new_callable=AsyncMock,
            ),
            patch(
                "tracecat.executor.service.prepare_resolved_context",
                new_callable=AsyncMock,
                return_value=prepared,
            ),
            patch(
                "tracecat.executor.service._invoke_step",
                new_callable=AsyncMock,
                side_effect=RuntimeError("infra failure"),
            ),
        ):
            input_ = _make_run_action_input()
            ctx = DispatchActionContext(role=_make_role())
            with pytest.raises(ExecutionError) as exc_info:
                await invoke_once(backend, input_, ctx)

            assert exc_info.value.info is not None
            assert exc_info.value.info.type == "RuntimeError"
            assert "infra failure" in exc_info.value.info.message


@pytest.mark.anyio
class TestDispatchAction:
    """Test dispatch_action for single actions and for_each loops."""

    async def test_single_action_dispatch(self) -> None:
        """Single action (no for_each) should call invoke_once directly."""
        role = _make_role()
        input_ = _make_run_action_input()
        backend = AsyncMock()

        resolved = _make_resolved_context()
        prepared = PreparedContext(resolved_context=resolved, mask_values=None)

        backend.execute.return_value = ExecutorResultSuccess(result="single_result")

        from tracecat.contexts import ctx_role

        token = ctx_role.set(role)
        try:
            with (
                patch(
                    "tracecat.executor.service.registry_resolver.prefetch_lock",
                    new_callable=AsyncMock,
                ),
                patch(
                    "tracecat.executor.service.prepare_resolved_context",
                    new_callable=AsyncMock,
                    return_value=prepared,
                ),
            ):
                result = await dispatch_action(backend, input_)
            assert result == "single_result"
        finally:
            ctx_role.reset(token)

    async def test_dispatch_without_role_raises(self) -> None:
        """dispatch_action should raise when ctx_role is not set."""
        from tracecat.contexts import ctx_role

        token = ctx_role.set(None)
        try:
            backend = AsyncMock()
            input_ = _make_run_action_input()
            with pytest.raises(ValueError, match="Role is required"):
                await dispatch_action(backend, input_)
        finally:
            ctx_role.reset(token)

    async def test_for_each_dispatch(self) -> None:
        """for_each should dispatch multiple invoke_once calls in parallel."""
        role = _make_role()
        task = _make_action_statement(
            args={"value": "${{ var.item }}"},
            for_each="${{ for var.item in TRIGGER.items }}",
        )
        context = _make_materialized_context(trigger={"items": ["x", "y"]})
        input_ = _make_run_action_input(task=task, exec_context=context)
        backend = AsyncMock()

        async def mock_invoke_once(
            _backend: Any,
            _input: Any,
            _ctx: Any,
            iteration: int | None = None,
        ) -> str:
            return f"result_{iteration}"

        from tracecat.contexts import ctx_role

        token = ctx_role.set(role)
        try:
            with patch(
                "tracecat.executor.service.invoke_once",
                side_effect=mock_invoke_once,
            ):
                results = await dispatch_action(backend, input_)
            assert len(results) == 2
        finally:
            ctx_role.reset(token)


@pytest.mark.anyio
class TestPrepareResolvedContext:
    """Test context resolution (secrets, variables, registry artifacts)."""

    async def test_prepare_resolved_context_success(self) -> None:
        """Should resolve all dependencies and return PreparedContext."""
        role = _make_role()
        input_ = _make_run_action_input()

        mock_action_impl = ActionImplementation(
            type="udf",
            action_name="core.transform.reshape",
            module="tracecat_registry.integrations.core.transform",
            name="reshape",
        )

        with (
            patch(
                "tracecat.executor.service.registry_resolver.resolve_action",
                new_callable=AsyncMock,
                return_value=mock_action_impl,
            ),
            patch(
                "tracecat.executor.service.registry_resolver.collect_action_secrets_from_manifest",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "tracecat.executor.service.collect_expressions",
                return_value=MagicMock(secrets=set(), variables=set()),
            ),
            patch(
                "tracecat.executor.service.secrets_manager.get_action_secrets",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "tracecat.executor.service.get_workspace_variables",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "tracecat.executor.service.mint_executor_token",
                return_value="test-jwt-token",
            ),
            patch(
                "tracecat.executor.service.config.TRACECAT__UNSAFE_DISABLE_SM_MASKING",
                False,
            ),
        ):
            from tracecat.executor.service import prepare_resolved_context

            result = await prepare_resolved_context(input_, role)

        assert isinstance(result, PreparedContext)
        assert result.resolved_context.action_impl == mock_action_impl
        assert result.resolved_context.executor_token == "test-jwt-token"
        assert result.resolved_context.evaluated_args == {"value": "hello"}
        assert result.mask_values == set()

    async def test_prepare_resolved_context_with_secrets(self) -> None:
        """Should collect mask values from resolved secrets."""
        role = _make_role()
        input_ = _make_run_action_input(
            task=_make_action_statement(
                args={"api_key": "${{ SECRETS.creds.API_KEY }}"}
            ),
        )

        mock_action_impl = ActionImplementation(
            type="udf",
            action_name="core.transform.reshape",
        )

        with (
            patch(
                "tracecat.executor.service.registry_resolver.resolve_action",
                new_callable=AsyncMock,
                return_value=mock_action_impl,
            ),
            patch(
                "tracecat.executor.service.registry_resolver.collect_action_secrets_from_manifest",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "tracecat.executor.service.collect_expressions",
                return_value=MagicMock(secrets={"creds.API_KEY"}, variables=set()),
            ),
            patch(
                "tracecat.executor.service.secrets_manager.get_action_secrets",
                new_callable=AsyncMock,
                return_value={"creds": {"API_KEY": "sk-super-secret"}},
            ),
            patch(
                "tracecat.executor.service.get_workspace_variables",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "tracecat.executor.service.mint_executor_token",
                return_value="jwt",
            ),
            patch(
                "tracecat.executor.service.config.TRACECAT__UNSAFE_DISABLE_SM_MASKING",
                False,
            ),
        ):
            from tracecat.executor.service import prepare_resolved_context

            result = await prepare_resolved_context(input_, role)

        assert result.mask_values is not None
        assert "sk-super-secret" in result.mask_values

    async def test_prepare_resolved_context_no_org_raises(self) -> None:
        """Should raise ValueError when organization_id is None."""
        role = _make_role()
        object.__setattr__(role, "organization_id", None)
        input_ = _make_run_action_input()

        from tracecat.executor.service import prepare_resolved_context

        with pytest.raises(ValueError, match="organization_id is required"):
            await prepare_resolved_context(input_, role)

    async def test_prepare_resolved_context_masking_disabled(self) -> None:
        """Should return mask_values=None when masking is disabled."""
        role = _make_role()
        input_ = _make_run_action_input()

        mock_action_impl = ActionImplementation(
            type="udf",
            action_name="core.transform.reshape",
        )

        with (
            patch(
                "tracecat.executor.service.registry_resolver.resolve_action",
                new_callable=AsyncMock,
                return_value=mock_action_impl,
            ),
            patch(
                "tracecat.executor.service.registry_resolver.collect_action_secrets_from_manifest",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "tracecat.executor.service.collect_expressions",
                return_value=MagicMock(secrets=set(), variables=set()),
            ),
            patch(
                "tracecat.executor.service.secrets_manager.get_action_secrets",
                new_callable=AsyncMock,
                return_value={"creds": {"KEY": "secret"}},
            ),
            patch(
                "tracecat.executor.service.get_workspace_variables",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "tracecat.executor.service.mint_executor_token",
                return_value="jwt",
            ),
            patch(
                "tracecat.executor.service.config.TRACECAT__UNSAFE_DISABLE_SM_MASKING",
                True,
            ),
        ):
            from tracecat.executor.service import prepare_resolved_context

            result = await prepare_resolved_context(input_, role)

        assert result.mask_values is None
