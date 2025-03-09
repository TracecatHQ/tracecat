from __future__ import annotations

from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tracecat.ee.enums import PlatformAction
from tracecat.ee.interactions.common import SignalState
from tracecat.ee.interactions.enums import SignalStatus
from tracecat.ee.interactions.models import (
    SignalHandlerInput,
    SignalHandlerResult,
    WaitResponseArgs,
)

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput
    from tracecat.dsl.models import ActionStatement
    from tracecat.dsl.workflow import DSLWorkflow


def prepare_signal_states(dsl: DSLInput) -> dict[str, SignalState]:
    """Prepare signal states for DSL actions."""
    # Signals
    # Prepare signal activation mappings
    # For each action in the DSL that's a `core.workflow.await_response` action,
    # we need to prepare a signal activation mapping
    signal_states: dict[str, SignalState] = {}
    for action in dsl.actions:
        if action.action == PlatformAction.WAIT_RESPONSE:
            act_args = WaitResponseArgs.model_validate(action.args)
            signal_states[act_args.ref] = SignalState(
                ref=action.ref,
                type=PlatformAction.WAIT_RESPONSE,
            )
    return signal_states


async def handle_wait_response_action(
    wf: DSLWorkflow, task: ActionStatement
) -> dict[str, Any]:
    # In a previous action like slack.send_message, we passed some kind of signal ID
    # into the slack block metadata which is passed over to the client. Ths signal ID
    # is some value that the client can use to send a signal back to the workflow.
    args = WaitResponseArgs.model_validate(task.args)
    sig_ref = args.ref

    # Start an activity asynchronously to wait for the signal
    # This is so that we can return a result immediately
    # and not block the workflow
    wf.logger.warning("Waiting for response", signal_ref=sig_ref)
    try:
        wf.signal_states[sig_ref].status = SignalStatus.PENDING
        # 1. Wait for receipt of an external signal
        await workflow.wait_condition(
            lambda: wf.signal_states[sig_ref].is_activated(),
            timeout=args.timeout,
        )
    except TimeoutError as e:
        raise ApplicationError(
            "Timeout waiting for response", non_retryable=True
        ) from e
    except Exception as e:
        wf.logger.error("Error waiting for response", signal_ref=sig_ref, exc=e)
        raise e
    wf.logger.warning("Received response", signal_ref=sig_ref)
    return wf.signal_states[sig_ref].data


def receive_signal(wf: DSLWorkflow, input: SignalHandlerInput) -> SignalHandlerResult:
    wf.logger.info("Received signal", input=input)
    if input.signal_id not in wf.signal_states:
        wf.logger.warning(
            "Received signal for unknown action", signal_id=input.signal_id
        )
        raise ApplicationError("Received signal for unknown action", non_retryable=True)
    wf.signal_states[input.signal_id].data = input.data
    wf.signal_states[input.signal_id].status = SignalStatus.COMPLETED
    return SignalHandlerResult(
        message="success",
        detail=input.data,
    )


def validate_signal(wf: DSLWorkflow, input: SignalHandlerInput) -> None:
    # Match the signal id and action ref
    if input.signal_id not in wf.signal_states:
        raise ValueError("Workflow signal receiver could not find signal state")
    state = wf.signal_states[input.signal_id]
    if state.ref != input.ref:
        raise ValueError("Workflow signal receiver received invalid signal")
