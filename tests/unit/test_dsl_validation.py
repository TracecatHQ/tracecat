from __future__ import annotations

import pytest

from tracecat import config
from tracecat.dsl.validation import (
    resolve_workflow_concurrency_limits_enabled_activity,
)
from tracecat.feature_flags import FeatureFlag


def test_resolve_workflow_concurrency_limits_enabled_activity_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__FEATURE_FLAGS",
        {FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS},
    )

    assert resolve_workflow_concurrency_limits_enabled_activity() is True


def test_resolve_workflow_concurrency_limits_enabled_activity_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())

    assert resolve_workflow_concurrency_limits_enabled_activity() is False
