"""Frozen templates from alpha.9.1 must remain executable on newer runtimes."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
import yaml
from pytest_mock import MockerFixture

from tracecat.auth.types import Role
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor import service
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.expressions.policy import build_provenance
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.actions.schemas import TemplateAction
from tracecat.registry.lock.types import RegistryLock

FIXTURES = Path(__file__).resolve().parents[1] / "data/registry/legacy_slack"


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["post_message", "update_message"])
async def test_locked_legacy_slack_template_reaches_sdk(
    name: str, mocker: MockerFixture
) -> None:
    # Keep the historical template frozen: loading today's YAML would miss the
    # incompatibility in already-published immutable registry manifests.
    template = TemplateAction.model_validate(
        yaml.safe_load((FIXTURES / f"{name}.yml").read_text())
    )
    action = template.definition.action
    role = Role(
        type="service",
        service_id="tracecat-executor",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
    )
    workflow_id = WorkflowUUID.new_uuid4()
    arguments = {"channel": "synthetic-channel", "text": "Synthetic message"}
    if name == "update_message":
        arguments["ts"] = "1234567890.123456"
    action_input = RunActionInput(
        task=ActionStatement(ref="send", action=action, args=arguments),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=workflow_id,
            wf_exec_id=generate_exec_id(workflow_id),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "historical-version"},
            actions={
                action: "tracecat_registry",
                "tools.slack_sdk.call_method": "tracecat_registry",
            },
        ),
    )
    resolved = ResolvedContext(
        action_impl=ActionImplementation(
            type="template",
            action_name=action,
            template_definition=template.definition.model_dump(mode="json"),
        ),
        evaluated_args=arguments,
        workspace_id=str(role.workspace_id),
        workflow_id=str(workflow_id),
        run_id=str(action_input.run_context.wf_run_id),
        executor_token="synthetic-parent-token",
    )
    mocker.patch.object(
        service.registry_resolver,
        "resolve_action",
        new=mocker.AsyncMock(
            return_value=ActionImplementation(
                type="udf", action_name="tools.slack_sdk.call_method"
            )
        ),
    )
    mocker.patch.object(
        service, "_mint_action_executor_token", return_value="synthetic-step-token"
    )
    backend = mocker.Mock()
    backend.execute = mocker.AsyncMock(
        return_value=ExecutorResultSuccess(result={"ok": True})
    )

    result = await service._execute_template_action(
        backend=backend,
        input=action_input,
        ctx=service.DispatchActionContext(role=role),
        resolved_context=resolved,
        timeout=30,
        provenance=build_provenance(arguments),
    )

    assert result == {"ok": True}
    backend.execute.assert_awaited_once()
    assert backend.execute.await_args is not None
    step = backend.execute.await_args.kwargs["resolved_context"]
    expected_method = "chat_postMessage" if name == "post_message" else "chat_update"
    assert step.evaluated_args["sdk_method"] == expected_method
    params = step.evaluated_args["params"]
    assert params["channel"] == "synthetic-channel"
    assert params["text"] == "Synthetic message"
    if name == "update_message":
        assert params["ts"] == arguments["ts"]
    key = "interaction" if name == "post_message" else "interaction_context"
    assert params["metadata"]["event_payload"][key] is None
