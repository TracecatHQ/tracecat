from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from temporalio import activity

from tracecat.admission.limits import get_effective_limits
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.identifiers import OrganizationID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.temporal.errors import raise_application_error_from_classification
from tracecat.tiers.exceptions import DefaultTierNotConfiguredError
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
    For other triggers (webhook, manual, case, form), uses the workflow start time.
    """
    if inputs.trigger_type == TriggerType.SCHEDULED and inputs.scheduled_start_time:
        return inputs.scheduled_start_time
    return inputs.start_time


@activity.defn
def resolve_workflow_concurrency_limits_enabled_activity() -> bool:
    """Resolve and freeze concurrency-limit flag state in workflow history.

    Pre-``ADMISSION_RUN_LIMITS`` histories only; new runs use
    ``resolve_run_limits_activity``.
    """
    return is_feature_enabled(FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS)


class RunLimitsSnapshot(BaseModel):
    """Per-run limits frozen into workflow history at the start of a run."""

    max_action_executions_per_workflow: int | None = None
    """Action budget for this run; ``None`` means unlimited."""
    max_concurrent_actions: int | None = None
    """The org's action limit at run start. The run throttle keeps at most this many
    action activities outstanding; ``None`` means no throttle. Histories recorded
    before this field decode it as ``None``."""


@activity.defn
async def resolve_run_limits_activity(org_id: OrganizationID) -> RunLimitsSnapshot:
    """Resolve the run's limits once, as a local activity.

    Replaces the flag snapshot plus a separate tier-limits activity. With the
    ``workflow-concurrency-limits`` flag off, nothing is limited and no lookup
    runs.
    """
    if not is_feature_enabled(FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS):
        return RunLimitsSnapshot()
    try:
        limits = await get_effective_limits(org_id)
    except Exception as e:
        invalid_data = isinstance(e, DefaultTierNotConfiguredError)
        classification = RuntimeErrorClassification.platform(
            kind=(
                RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA
                if invalid_data
                else RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE
            ),
            message="Tracecat could not resolve workflow tier limits",
            retry_disposition=(
                RetryDisposition.NON_RETRYABLE
                if invalid_data
                else RetryDisposition.RETRYABLE
            ),
            cause=e,
        )
        raise_application_error_from_classification(classification)
    return RunLimitsSnapshot(
        max_action_executions_per_workflow=limits.max_action_executions_per_workflow,
        max_concurrent_actions=limits.max_concurrent_actions,
    )
