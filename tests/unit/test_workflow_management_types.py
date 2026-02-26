from datetime import timedelta

from tracecat.workflow.management.types import build_workflow_trigger_summary


def test_build_workflow_trigger_summary_returns_none_without_trigger_metadata() -> None:
    summary = build_workflow_trigger_summary(
        online_schedule_count=0,
        schedule_cron=None,
        schedule_every=None,
        webhook_active=False,
        case_trigger_event_types=None,
    )

    assert summary is None


def test_build_workflow_trigger_summary_keeps_cron_when_only_offline_schedule_exists() -> (
    None
):
    summary = build_workflow_trigger_summary(
        online_schedule_count=0,
        schedule_cron="0 0 1 * *",
        schedule_every=None,
        webhook_active=False,
        case_trigger_event_types=None,
    )

    assert summary is not None
    assert summary.schedule_count_online == 0
    assert summary.schedule_cron == "0 0 1 * *"
    assert summary.schedule_natural == "Cron 0 0 1 * *"
    assert summary.webhook_active is False
    assert summary.case_trigger_events == ()


def test_build_workflow_trigger_summary_keeps_interval_when_only_offline_schedule_exists() -> (
    None
):
    summary = build_workflow_trigger_summary(
        online_schedule_count=0,
        schedule_cron=None,
        schedule_every=timedelta(days=2),
        webhook_active=False,
        case_trigger_event_types=None,
    )

    assert summary is not None
    assert summary.schedule_count_online == 0
    assert summary.schedule_cron is None
    assert summary.schedule_natural == "Every 2d"
    assert summary.webhook_active is False
    assert summary.case_trigger_events == ()
