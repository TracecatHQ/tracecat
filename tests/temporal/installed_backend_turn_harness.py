"""An installed agent backend whose turn parks on one approval, then answers.

Kept apart from the test module so the workflow sandbox imports only this.
"""

from typing import Any

from temporalio import workflow

from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.schemas import (
    AgentWorkflowArgs,
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat.agent.backends.types import (
    SessionForkContext,
    SessionWorkflowContext,
    TurnResult,
)
from tracecat.agent.schemas import AgentOutput

INSTALLED_TURN_WORKFLOW = "InstalledBackendTurnWorkflow"


@workflow.defn(name=INSTALLED_TURN_WORKFLOW)
class InstalledBackendTurnWorkflow:
    def __init__(self) -> None:
        self._approvals: dict[str, Any] | None = None

    @workflow.run
    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        await workflow.wait_condition(lambda: self._approvals is not None)
        assert self._approvals is not None
        # Like a real backend, a turn that ends without output still completes.
        if self._approvals.get("call_1") is not True:
            return {"error": "The tool call was denied"}
        return {
            "output": {
                "verdict": "benign",
                "prompt": args["prompt"],
                "output_type": args["output_type"],
            }
        }

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> bool:
        self._approvals = dict(submission.approvals)
        return True

    @workflow.update
    def request_cancel(self, request: WorkflowCancelRequest) -> None:
        del request


class InstalledBackend(AgentBackend[dict[str, Any], dict[str, Any]]):
    workflow = InstalledBackendTurnWorkflow
    name = "Installed"
    default_harness = "installed"
    supported_harnesses = frozenset({"installed"})
    caller_owned_turns = True
    task_queue = "unused"

    async def prepare_fork(self, context: SessionForkContext) -> None:
        del context

    async def build_workflow_args(
        self, context: SessionWorkflowContext
    ) -> dict[str, Any]:
        return {"prompt": context.prompt, "output_type": context.config.output_type}

    def parse_turn_result(self, result: object) -> TurnResult:
        match result:
            case {"output": output}:
                return TurnResult(output=output)
            case {"error": str(error)}:
                return TurnResult(output=None, error=error)
        return TurnResult(output=None, error="Turn ended without output")


@workflow.defn(name="DurableAgentWorkflow")
class BuiltinAgentWorkflow:
    """Stands in for the built-in durable agent workflow."""

    @workflow.run
    async def run(self, args: AgentWorkflowArgs) -> AgentOutput:
        return AgentOutput(
            output="built-in output",
            duration=0,
            session_id=args.agent_args.session_id,
        )
