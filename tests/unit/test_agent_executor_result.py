from tracecat.agent.executor.activity import AgentExecutorResult


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
