from __future__ import annotations

from tracecat import config
from tracecat.dsl.workflow import (
    DSLWorkflow,
    DSLWorkflowV2,
    dsl_workflow_run_method_for_new_execution,
    dsl_workflow_type_name_for_new_execution,
    is_dsl_workflow_type_name,
)
from tracecat.feature_flags.enums import FeatureFlag


def _workflow_type_name(workflow_cls: type) -> str:
    definition = getattr(workflow_cls, "__temporal_workflow_definition")
    return definition.name


def test_dsl_workflow_v2_is_distinct_temporal_workflow_type() -> None:
    assert _workflow_type_name(DSLWorkflow) == "DSLWorkflow"
    assert _workflow_type_name(DSLWorkflowV2) == "DSLWorkflowV2"
    assert is_dsl_workflow_type_name("DSLWorkflow")
    assert is_dsl_workflow_type_name("DSLWorkflowV2")


def test_new_execution_target_defaults_to_v1(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())

    assert dsl_workflow_run_method_for_new_execution() == DSLWorkflow.run
    assert dsl_workflow_type_name_for_new_execution() == "DSLWorkflow"


def test_new_execution_target_uses_v2_when_feature_flag_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.DSL_WORKFLOW_V2}
    )

    assert dsl_workflow_run_method_for_new_execution() == DSLWorkflowV2.run
    assert dsl_workflow_type_name_for_new_execution() == "DSLWorkflowV2"
