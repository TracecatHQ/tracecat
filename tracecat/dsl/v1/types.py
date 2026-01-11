from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field

from tracecat.dsl.common import ExecuteChildWorkflowArgs
from tracecat.dsl.schemas import DSLEnvironment
from tracecat.dsl.workflow import ExprContext, RunAgentArgs
from tracecat.identifiers import WorkflowUUID
from tracecat.storage.object import InlineObject, StoredObject

type ActionContext = dict[str, ActionOutcome]
type MaterializedContext = dict[ExprContext, Any]
"""Immediately can be passed into expr resolution as operand"""


class ExecutionContext(BaseModel):
    actions: ActionContext = Field(default_factory=dict)
    trigger: StoredObject | None = None
    env: DSLEnvironment = Field(default_factory=DSLEnvironment)


# -----------------------------------------------------------------------------
# ActionOutcome Variants
# -----------------------------------------------------------------------------

"""
Notes
- Instead of Success/Error, should we just have Completed that just wraps TaskResult?
"""


class ActionSuccess(BaseModel):
    kind: Literal["success"] = Field(default="success", frozen=True)
    result: StoredObject


class ActionFailure(BaseModel):
    kind: Literal["failure"] = Field(default="failure", frozen=True)
    error: InlineObject


class ActionSkip(BaseModel):
    kind: Literal["skip"] = Field(default="skip", frozen=True)
    reason: Literal["condition-not-met", "propagation"] | None = None


# -----------------------------------------------------------------------------
# Control-Flow Action Outcomes (Scatter/Gather)
# -----------------------------------------------------------------------------


class ActionScatter(BaseModel):
    """Outcome for a scatter control-flow action.

    Scatter evaluates a collection and creates execution streams. The actual
    items are stored in a manifest to avoid history bloat.
    """

    kind: Literal["scatter"] = Field(default="scatter", frozen=True)
    count: int = 0
    data: StoredObject


class ActionGather(BaseModel):
    """Outcome for a gather control-flow action.

    Gather synchronizes execution streams and collects results. Like scatter,
    we return a count to keep the outcome small - actual results stay in context.
    """

    kind: Literal["gather"] = Field(default="gather", frozen=True)
    count: int = 0
    data: StoredObject


class ActionRetry(BaseModel):
    """Signals to retry the action. Returned if retry-until condition is not met."""

    kind: Literal["retry"] = Field(default="retry", frozen=True)
    reason: Literal["retry-until-not-met"] | None = None


# -----------------------------------------------------------------------------
# ActionOutcome Union Type
# -----------------------------------------------------------------------------

ActionOutcome = Annotated[
    ActionSuccess
    | ActionFailure
    | ActionSkip
    | ActionScatter
    | ActionGather
    | ActionRetry,
    Discriminator("kind"),
]


# Platform directives
class ExecuteSubflow(BaseModel):
    kind: Literal["execute-subflow"] = Field(default="execute-subflow", frozen=True)
    workflow_id: WorkflowUUID
    data: StoredObject
    args: ExecuteChildWorkflowArgs


class ExecuteAgent(BaseModel):
    kind: Literal["execute-agent"] = Field(default="execute-agent", frozen=True)
    data: StoredObject
    args: RunAgentArgs


PlatformDirective = Annotated[ExecuteSubflow | ExecuteAgent, Discriminator("kind")]
ExecuteTaskResult = Annotated[ActionOutcome | PlatformDirective, Discriminator("kind")]
