from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Annotated, Any

import pytest
from pydantic import Field

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor import service as executor_service
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.executor.service import _apply_manifest_arg_defaults
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.repository import RegisterKwargs, generate_model_from_function
from tracecat.registry.versions.schemas import RegistryVersionManifestAction

_ACTION_NAME = "testing.create_ticket"


def _action_impl() -> ActionImplementation:
    return ActionImplementation(
        type="udf",
        action_name=_ACTION_NAME,
        module="testing",
        name="create_ticket",
        origin="tracecat_registry",
    )


def _manifest_action(expects: dict[str, object]) -> RegistryVersionManifestAction:
    return RegistryVersionManifestAction(
        namespace="testing",
        name="create_ticket",
        action_type="udf",
        description="Test",
        interface={"expects": expects, "returns": None},
        implementation={"type": "udf", "module": "testing", "name": "create_ticket"},
    )


def _optional_arg_manifest_action() -> RegistryVersionManifestAction:
    def create_ticket(
        summary: Annotated[str, Field(..., description="Required summary")],
        client_id: Annotated[
            int | None, Field(default=None, description="Optional client")
        ],
        priority: Annotated[int, Field(default=3, description="Priority")],
    ) -> dict[str, str | int | None]:
        return {"summary": summary, "client_id": client_id, "priority": priority}

    input_model, rtype, rtype_adapter = generate_model_from_function(
        create_ticket,
        RegisterKwargs(
            namespace="testing",
            default_title=None,
            description="Test",
            display_group=None,
        ),
    )
    bound_action = BoundRegistryAction(
        fn=create_ticket,
        type="udf",
        name="create_ticket",
        namespace="testing",
        description="Test",
        secrets=None,
        args_cls=input_model,
        args_docs={},
        rtype_cls=rtype,
        rtype_adapter=rtype_adapter,
        default_title=None,
        display_group=None,
        doc_url=None,
        author=None,
        deprecated=None,
        origin="tracecat_registry",
    )

    return _manifest_action(bound_action.get_interface()["expects"])


def _registry_lock() -> RegistryLock:
    return RegistryLock(
        origins={"tracecat_registry": "test-version"},
        actions={_ACTION_NAME: "tracecat_registry"},
    )


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


def _run_action_input(args: dict[str, Any]) -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    exec_id = ExecutionUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(action=_ACTION_NAME, args=args, ref="create_ticket"),
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=_registry_lock(),
    )


def _parent_resolved_context() -> ResolvedContext:
    return ResolvedContext(
        secrets={},
        variables={},
        action_impl=_action_impl(),
        evaluated_args={},
        workspace_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        executor_token="parent-token",
        logical_time=datetime.now(UTC),
        secret_projection=SecretEnvProjection(env={}, mask_values=set()),
    )


def _patch_prepare_context_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve_action(
        action_name: str,
        registry_lock: RegistryLock,
        organization_id: uuid.UUID,
    ) -> ActionImplementation:
        assert action_name == _ACTION_NAME
        assert registry_lock == _registry_lock()
        assert organization_id is not None
        return _action_impl()

    async def resolve_manifest_action(
        action_name: str,
        registry_lock: RegistryLock,
        organization_id: uuid.UUID,
    ) -> RegistryVersionManifestAction:
        assert action_name == _ACTION_NAME
        assert registry_lock == _registry_lock()
        assert organization_id is not None
        return _optional_arg_manifest_action()

    async def collect_action_secrets_from_manifest(
        action_name: str,
        registry_lock: RegistryLock,
        organization_id: uuid.UUID,
    ) -> set[str]:
        assert action_name == _ACTION_NAME
        assert registry_lock == _registry_lock()
        assert organization_id is not None
        return set()

    async def get_action_secrets(
        *,
        secret_exprs: Iterable[str],
        action_secrets: set[str],
    ) -> dict[str, Any]:
        assert not list(secret_exprs)
        assert action_secrets == set()
        return {}

    async def get_workspace_variables(
        *,
        variable_exprs: Iterable[str],
        environment: str,
        role: Role,
    ) -> dict[str, Any]:
        assert not list(variable_exprs)
        assert environment == "default"
        assert role.organization_id is not None
        return {}

    async def project_secret_env(
        *,
        secrets: dict[str, Any],
        role: Role,
        run_context: RunContext,
    ) -> SecretEnvProjection:
        assert secrets == {}
        assert role.organization_id is not None
        assert run_context.environment == "default"
        return SecretEnvProjection(env={}, mask_values=set())

    monkeypatch.setattr(
        executor_service.registry_resolver, "resolve_action", resolve_action
    )
    monkeypatch.setattr(
        executor_service.registry_resolver,
        "resolve_manifest_action",
        resolve_manifest_action,
    )
    monkeypatch.setattr(
        executor_service.registry_resolver,
        "collect_action_secrets_from_manifest",
        collect_action_secrets_from_manifest,
    )
    monkeypatch.setattr(
        executor_service.secrets_manager, "get_action_secrets", get_action_secrets
    )
    monkeypatch.setattr(
        executor_service, "get_workspace_variables", get_workspace_variables
    )
    monkeypatch.setattr(executor_service, "project_secret_env", project_secret_env)
    monkeypatch.setattr(executor_service, "mint_executor_token", lambda **_: "token")


@pytest.mark.regression
def test_apply_manifest_arg_defaults_preserves_schema_defaults() -> None:
    manifest_action = _optional_arg_manifest_action()

    materialized_args = _apply_manifest_arg_defaults(
        manifest_action=manifest_action,
        evaluated_args={"summary": "Ticket without client_id"},
    )

    assert materialized_args == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }


@pytest.mark.regression
def test_apply_manifest_arg_defaults_preserves_existing_args_and_python_values() -> None:
    logical_time = datetime.now(UTC)
    manifest_action = _manifest_action(
        {
            "type": "object",
            "properties": {
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "payload": {"type": "object"},
                "logical_time": {"type": "string", "format": "date-time"},
                "client_id": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                },
            },
            "required": ["headers", "payload", "logical_time"],
        }
    )

    materialized_args = _apply_manifest_arg_defaults(
        manifest_action=manifest_action,
        evaluated_args={
            "headers": {"Authorization": "Bearer token"},
            "payload": {"nested": {"count": 1}},
            "logical_time": logical_time,
        },
    )

    assert materialized_args == {
        "headers": {"Authorization": "Bearer token"},
        "payload": {"nested": {"count": 1}},
        "logical_time": logical_time,
        "client_id": None,
    }


@pytest.mark.anyio
@pytest.mark.regression
async def test_prepare_resolved_context_applies_manifest_arg_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_context_dependencies(monkeypatch)

    prepared = await executor_service.prepare_resolved_context(
        input=_run_action_input({"summary": "Ticket without client_id"}),
        role=_role(),
    )

    assert prepared.resolved_context.evaluated_args == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }


@pytest.mark.anyio
@pytest.mark.regression
async def test_prepare_step_context_applies_manifest_arg_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_prepare_context_dependencies(monkeypatch)

    resolved = await executor_service._prepare_step_context(
        step_action=_ACTION_NAME,
        evaluated_args={"summary": "Ticket without client_id"},
        parent_resolved=_parent_resolved_context(),
        input=_run_action_input({}),
        role=_role(),
    )

    assert resolved.evaluated_args == {
        "summary": "Ticket without client_id",
        "client_id": None,
        "priority": 3,
    }
