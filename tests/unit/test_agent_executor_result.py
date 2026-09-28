from tracecat.agent.error_policy import agent_executor_unavailable
from tracecat.agent.executor.activity import AgentExecutorResult
from tracecat.observability.types import PlatformErrorCapture


def test_agent_executor_result_legacy_structured_output_alias() -> None:
    result = AgentExecutorResult.model_validate(
        {"success": True, "structured_output": "legacy"}
    )

    assert result.output == "legacy"


def test_agent_executor_result_legacy_result_output_alias() -> None:
    result = AgentExecutorResult.model_validate(
        {"success": True, "result_output": "legacy2"}
    )

    assert result.output == "legacy2"


def test_source_capture_roundtrips_in_workflow_results() -> None:
    result = AgentExecutorResult(
        success=False,
        sentry_capture=PlatformErrorCapture.for_error(
            "a" * 32, agent_executor_unavailable()
        ),
    )
    assert (
        AgentExecutorResult.model_validate_json(result.model_dump_json()).sentry_capture
        == result.sentry_capture
    )
