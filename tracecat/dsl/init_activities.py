from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from temporalio import activity

from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.workflow.executions.enums import TriggerType


class ResolveTimeAnchorActivityInputs(BaseModel):
    """Inputs for resolving the workflow time anchor."""

    trigger_type: TriggerType
    start_time: datetime
    scheduled_start_time: datetime | None = None


@activity.defn
def resolve_time_anchor_activity(
    inputs: ResolveTimeAnchorActivityInputs,
) -> datetime:
    """Resolve the time anchor based on trigger type.

    This activity is recorded in workflow history and replayed on reset,
    ensuring the same time anchor is used across workflow resets.

    For scheduled workflows, uses TemporalScheduledStartTime (the intended schedule time).
    For other triggers (webhook, manual, case), uses the workflow start time.
    """
    if inputs.trigger_type == TriggerType.SCHEDULED and inputs.scheduled_start_time:
        return inputs.scheduled_start_time
    return inputs.start_time


@activity.defn
def resolve_workflow_concurrency_limits_enabled_activity() -> bool:
    """Resolve and freeze concurrency-limit flag state in workflow history."""
    return is_feature_enabled(FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS)
