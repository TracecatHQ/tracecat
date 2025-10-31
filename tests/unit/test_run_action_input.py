import uuid

from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.expressions.common import ExprContext
from tracecat.identifiers.workflow import ExecutionUUID, WorkflowUUID


def test_run_action_input_drops_legacy_inputs_context():
    wf_id = WorkflowUUID.new_uuid4()
    exec_id = ExecutionUUID.new_uuid4()
    run_input = RunActionInput(
        task=ActionStatement(
            action="core.transform.reshape",
            args={"value": 1},
            ref="reshape",
        ),
        exec_context={
            "INPUTS": {"legacy": True},  # pyright: ignore[reportArgumentType]
            ExprContext.ACTIONS: {},
            ExprContext.TRIGGER: {},
            ExprContext.ENV: {},
        },
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/{exec_id.short()}",
            wf_run_id=uuid.uuid4(),
            environment="test",
        ),
    )

    assert "INPUTS" not in run_input.exec_context
    assert ExprContext.ACTIONS in run_input.exec_context
