from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from tracecat.agent.preset.activities import (
    resolve_agent_preset_config_activity,
    resolve_agent_preset_version_ref_activity,
)
from tracecat.agent.worker import get_activities as get_agent_worker_activities
from tracecat.dsl.worker import get_activities as get_dsl_worker_activities


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


def _activity_name(activity: object) -> str:
    return getattr(activity, "__temporal_activity_definition").name


def _activity_names(activities: Sequence[object]) -> set[str]:
    return {_activity_name(activity) for activity in activities}


def test_dsl_worker_registers_preset_version_resolution_activity() -> None:
    names = _activity_names(get_dsl_worker_activities())
    assert _activity_name(resolve_agent_preset_version_ref_activity) in names


def test_agent_worker_registers_preset_resolution_activities() -> None:
    names = _activity_names(get_agent_worker_activities())
    assert _activity_name(resolve_agent_preset_config_activity) in names
    assert _activity_name(resolve_agent_preset_version_ref_activity) in names
