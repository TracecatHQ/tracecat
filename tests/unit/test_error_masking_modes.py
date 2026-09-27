"""Masking mode resolution and conservative execution boundaries."""

import asyncio
import base64
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.contexts import ctx_error_masking, ctx_role, ctx_secret_masks
from tracecat.db.models import OrganizationSetting
from tracecat.dsl import action as dsl_action
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import ExecutionError, TracecatExpressionError
from tracecat.executor import minimal_runner, service
from tracecat.executor.schemas import ActionImplementation, ExecutorActionErrorInfo
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.expressions.eval import eval_templated_object
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox import service as sandbox_service
from tracecat.sandbox.exceptions import SandboxExecutionError
from tracecat.sandbox.types import SandboxResult
from tracecat.secrets.diagnostics import sanitize_diagnostic
from tracecat.secrets.error_masking import (
    WITHHELD_ERROR_MESSAGE,
    ErrorMaskingContext,
    ErrorMaskingMode,
)
from tracecat.secrets.masking import SecretMaskCollector
from tracecat.settings import service as settings_service
from tracecat.settings.schemas import AppSettingsUpdate
from tracecat.workspaces.schemas import WorkspaceSettingsUpdate

SECRET = "synthetic-mode-secret"
ENCODED = base64.b64encode(SECRET.encode()).decode()
ROLE = Role(
    service_id="tracecat-executor",
    type="service",
    organization_id=UUID(int=1),
    workspace_id=UUID(int=2),
)


@contextmanager
def masking_mode(
    mode: ErrorMaskingMode, *, sensitive: bool = True
) -> Iterator[SecretMaskCollector]:
    policy_token = ctx_error_masking.set(ErrorMaskingContext(mode, sensitive))
    masks = SecretMaskCollector()
    masks_token = ctx_secret_masks.set(masks)
    try:
        yield masks
    finally:
        ctx_error_masking.reset(policy_token)
        ctx_secret_masks.reset(masks_token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("workspace", "organization", "expected"),
    [
        ({}, "provenance", "provenance"),
        (None, "provenance", "provenance"),
        ({}, "conservative", "conservative"),
        ({"error_masking_mode": None}, "conservative", "conservative"),
        ({"error_masking_mode": "provenance"}, "conservative", "provenance"),
        ({"error_masking_mode": "conservative"}, "provenance", "conservative"),
        ({"error_masking_mode": "unsafe"}, "provenance", "conservative"),
        ({"error_masking_mode": False}, "provenance", "conservative"),
        ([], "provenance", "conservative"),
        ({}, "unsafe", "conservative"),
        ({}, None, "conservative"),
    ],
)
async def test_resolve_modes(monkeypatch, workspace, organization, expected):
    session = Mock(
        execute=AsyncMock(return_value=Mock(scalar_one=Mock(return_value=workspace)))
    )
    org_lookup = AsyncMock(side_effect=lambda *_args: ErrorMaskingMode(organization))
    monkeypatch.setattr(settings_service, "_read_error_masking_default", org_lookup)
    result = await settings_service.resolve_error_masking_mode(
        organization_id=UUID(int=1), workspace_id=UUID(int=2), session=session
    )
    assert result == expected
    query = session.execute.call_args.args[0].compile()
    assert UUID(int=1) in query.params.values()
    assert UUID(int=2) in query.params.values()
    if org_lookup.called:
        assert org_lookup.call_args.args[0] == UUID(int=1)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["workspace", "organization", "session"])
async def test_lookup_errors_fail_closed(monkeypatch, failure):
    if failure == "session":
        monkeypatch.setattr(
            settings_service,
            "get_async_session_bypass_rls_context_manager",
            Mock(side_effect=RuntimeError("unavailable")),
        )
        result = await settings_service.get_error_masking_mode(ROLE)
    else:
        session = Mock(
            execute=AsyncMock(return_value=Mock(scalar_one=Mock(return_value={})))
        )
        if failure == "workspace":
            session.execute.side_effect = RuntimeError("unavailable")
        monkeypatch.setattr(
            settings_service,
            "_read_error_masking_default",
            AsyncMock(side_effect=ValueError("malformed JSON")),
        )
        result = await settings_service.resolve_error_masking_mode(
            organization_id=UUID(int=1), workspace_id=UUID(int=2), session=session
        )
    assert result is ErrorMaskingMode.CONSERVATIVE


def test_settings_contracts_do_not_reinterpret_unsafe_flags():
    assert AppSettingsUpdate().app_error_masking_mode is ErrorMaskingMode.PROVENANCE
    assert WorkspaceSettingsUpdate().error_masking_mode is None
    assert WorkspaceSettingsUpdate(error_masking_mode=None).model_dump(
        exclude_unset=True
    ) == {"error_masking_mode": None}
    assert "error_masking_mode" not in WorkspaceSettingsUpdate().model_dump(
        exclude_unset=True
    )
    for schema, key in [
        (AppSettingsUpdate, "app_error_masking_mode"),
        (WorkspaceSettingsUpdate, "error_masking_mode"),
    ]:
        with pytest.raises(ValidationError):
            schema.model_validate({key: "unsafe"})
    assert (
        AppSettingsUpdate.model_validate(
            {
                "app_unsafe_disable_secret_error_withholding_workspace_ids": [
                    str(UUID(int=2))
                ]
            }
        ).app_error_masking_mode
        is ErrorMaskingMode.PROVENANCE
    )


@pytest.mark.parametrize(
    "source",
    [
        "SECRETS.api.TOKEN",
        "ACTIONS.fetch.result",
        "var.item",
        "inputs.token",
        "steps.fetch.result",
        "TRIGGER.token",
    ],
)
def test_conservative_expressions_disable_observer(monkeypatch, source):
    monkeypatch.setattr(
        "tracecat.expressions.policy.SecretValueObserver",
        Mock(side_effect=AssertionError("observer must be disabled")),
    )
    operand = {
        "SECRETS": {"api": {"TOKEN": SECRET}},
        "ACTIONS": {"fetch": {"result": SECRET}},
        "var": {"item": SECRET},
        "inputs": {"token": SECRET},
        "steps": {"fetch": {"result": SECRET}},
        "TRIGGER": {"token": SECRET},
    }
    with masking_mode(ErrorMaskingMode.CONSERVATIVE) as masks:
        with pytest.raises(TracecatExpressionError) as caught:
            eval_templated_object(
                "${{ int(FN.to_base64(" + source + ")) }}", operand=operand
            )
        assert str(caught.value) == WITHHELD_ERROR_MESSAGE
        assert caught.value.__context__ is None
        assert ENCODED not in masks.values
        # A literal-only failure remains useful, even alongside unrelated secrets.
        with pytest.raises(TracecatExpressionError, match="not-a-number"):
            eval_templated_object("${{ int('not-a-number') }}", operand=operand)


@pytest.mark.parametrize("mode", list(ErrorMaskingMode))
def test_runner_transformed_exception_and_output(monkeypatch, mode):
    def run(*_args):
        print(ENCODED)
        return "done"

    monkeypatch.setattr(minimal_runner, "run_action_minimal", run)
    notice = Mock()
    monkeypatch.setattr(minimal_runner, "_emit_suppressed_output_notice", notice)
    payload = {
        "resolved_context": {
            "action_impl": {"type": "udf", "name": "probe"},
            "evaluated_args": {},
        },
        "secret_env": {"TOKEN": SECRET},
        "withhold_error_details": mode is ErrorMaskingMode.CONSERVATIVE,
    }
    assert minimal_runner.main_minimal(payload)["success"]
    if mode is ErrorMaskingMode.CONSERVATIVE:
        assert ENCODED not in str(notice.call_args)

    def fail(*_args):
        raise ValueError(ENCODED)

    monkeypatch.setattr(minimal_runner, "run_action_minimal", fail)
    result = minimal_runner.main_minimal(payload)
    assert not result["success"]
    if mode is ErrorMaskingMode.CONSERVATIVE:
        assert result["error"]["message"] == WITHHELD_ERROR_MESSAGE
        assert ENCODED not in str(result)
    else:
        # Documents the limit of provenance for opaque code that never returns.
        assert ENCODED in result["error"]["message"]


def action_input(args):
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(ref="probe", action="testing.probe", args=args),
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


@pytest.mark.anyio
@pytest.mark.parametrize("action_name", ["testing.probe", "core.script.run_python"])
async def test_invocation_mode_and_sensitivity_isolation(monkeypatch, action_name):
    async def mode_for_role(role):
        return (
            ErrorMaskingMode.CONSERVATIVE
            if role.workspace_id == UUID(int=2)
            else ErrorMaskingMode.PROVENANCE
        )

    monkeypatch.setattr(service, "get_error_masking_mode", mode_for_role)
    monkeypatch.setattr(service.registry_resolver, "prefetch_lock", AsyncMock())
    monkeypatch.setattr(
        service.registry_resolver,
        "resolve_action",
        AsyncMock(
            return_value=ActionImplementation(type="udf", action_name=action_name)
        ),
    )
    monkeypatch.setattr(
        service.registry_resolver,
        "collect_action_secrets_from_manifest",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        service.secrets_manager, "get_action_secrets", AsyncMock(return_value={})
    )
    monkeypatch.setattr(service, "get_workspace_variables", AsyncMock(return_value={}))
    monkeypatch.setattr(
        service,
        "project_secret_env",
        AsyncMock(return_value=SecretEnvProjection(env={}, mask_values=set())),
    )
    monkeypatch.setattr(
        service, "_mint_action_executor_token", Mock(return_value="synthetic-token")
    )

    async def execute(**kwargs):
        await asyncio.sleep(0)
        raise ValueError("public diagnostic")

    backend = Mock(execute=execute)

    async def invoke(workspace, args):
        with pytest.raises(ExecutionError) as caught:
            await service.invoke_once(
                backend,
                action_input(args).model_copy(
                    update={
                        "task": ActionStatement(
                            ref="probe", action=action_name, args=args
                        )
                    }
                ),
                service.DispatchActionContext(
                    ROLE.model_copy(update={"workspace_id": UUID(int=workspace)})
                ),
            )
        assert ctx_error_masking.get() is None
        assert ctx_secret_masks.get() is None
        assert caught.value.__context__ is None
        return str(caught.value)

    conservative, public, provenance, credential_only = await asyncio.gather(
        invoke(2, {"value": "${{ TRIGGER }}"}),
        invoke(2, {"value": "${{ int('public diagnostic') }}"}),
        invoke(4, {"value": "${{ TRIGGER }}"}),
        invoke(2, {"value": "literal"}),
    )
    assert WITHHELD_ERROR_MESSAGE in conservative
    assert WITHHELD_ERROR_MESSAGE in credential_only
    assert "public diagnostic" in public
    assert "public diagnostic" in provenance


def test_conservative_transport_drops_untrusted_metadata():
    info = ExecutorActionErrorInfo(
        action_name="testing.probe",
        type=ENCODED,
        filename=ENCODED,
        function=ENCODED,
        message=ENCODED,
        lineno=42,
        loop_iteration=3,
        loop_vars={"value": ENCODED},
    )
    with masking_mode(ErrorMaskingMode.CONSERVATIVE):
        safe = service._sanitize_error_info(info, None)
        assert ENCODED not in safe.model_dump_json()
        assert safe.lineno == 42 and safe.loop_iteration == 3
        assert sanitize_diagnostic({"stderr": ENCODED}) == WITHHELD_ERROR_MESSAGE


@pytest.mark.anyio
@pytest.mark.parametrize("stored", [b"null", b"false", b'"invalid"', b"{broken"])
async def test_actual_malformed_organization_value_fails_closed(monkeypatch, stored):
    setting = OrganizationSetting(
        key="app_error_masking_mode", value=stored, is_encrypted=False
    )
    session = Mock(
        execute=AsyncMock(
            side_effect=[
                Mock(scalar_one=Mock(return_value={})),
                Mock(scalar_one_or_none=Mock(return_value=setting)),
            ]
        )
    )
    monkeypatch.setattr(
        settings_service, "get_setting_override", Mock(return_value=None)
    )
    assert (
        await settings_service.resolve_error_masking_mode(
            organization_id=UUID(int=1), workspace_id=UUID(int=2), session=session
        )
        is ErrorMaskingMode.CONSERVATIVE
    )


@pytest.mark.anyio
async def test_missing_organization_setting_uses_provenance(monkeypatch):
    session = Mock(
        execute=AsyncMock(
            side_effect=[
                Mock(scalar_one=Mock(return_value={})),
                Mock(scalar_one_or_none=Mock(return_value=None)),
            ]
        )
    )
    monkeypatch.setattr(
        settings_service, "get_setting_override", Mock(return_value=None)
    )
    assert (
        await settings_service.resolve_error_masking_mode(
            organization_id=UUID(int=1), workspace_id=UUID(int=2), session=session
        )
        is ErrorMaskingMode.PROVENANCE
    )


@pytest.mark.anyio
async def test_run_python_host_logs_withhold_transformed_diagnostics(
    monkeypatch, tmp_path
):
    sandbox = sandbox_service.SandboxService(cache_dir=str(tmp_path))
    monkeypatch.setattr(sandbox, "_is_nsjail_available", lambda: False)
    sandbox._unsafe_pid_executor = Mock(
        execute=AsyncMock(
            return_value=SandboxResult(
                success=False, error=ENCODED, stdout=ENCODED, stderr=ENCODED
            )
        )
    )
    logs = Mock()
    monkeypatch.setattr(sandbox_service, "logger", logs)
    with masking_mode(ErrorMaskingMode.CONSERVATIVE):
        with pytest.raises(SandboxExecutionError):
            await sandbox.run_python(
                script="def main():\n    return 1", inputs={"token": SECRET}
            )
    assert logs.error.called
    assert ENCODED not in str(logs.error.call_args_list)


@pytest.mark.parametrize("mode", list(ErrorMaskingMode))
def test_workflow_expression_activity_uses_workspace_mode(monkeypatch, mode):
    monkeypatch.setattr(
        dsl_action, "get_error_masking_mode", AsyncMock(return_value=mode)
    )
    monkeypatch.setattr(
        dsl_action,
        "materialize_context",
        AsyncMock(return_value={"ACTIONS": {"fetch": {"result": SECRET}}}),
    )
    role_token = ctx_role.set(ROLE)
    try:
        with masking_mode(ErrorMaskingMode.PROVENANCE) as parent_masks:
            with pytest.raises(ApplicationError) as caught:
                dsl_action.DSLActivities.evaluate_single_expression_activity(
                    "${{ int(FN.to_base64(ACTIONS.fetch.result)) }}",
                    create_default_execution_context(),
                )
            assert ctx_secret_masks.get() is parent_masks
            restored = ctx_error_masking.get()
            assert restored is not None
            assert restored.mode is ErrorMaskingMode.PROVENANCE
        serialized = str(caught.value) + str(caught.value.details)
        if mode is ErrorMaskingMode.CONSERVATIVE:
            assert ENCODED not in serialized
    finally:
        ctx_role.reset(role_token)
