from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tracecat.agent.diagnostics import LLMErrorDiagnostics
from tracecat.agent.error_policy import agent_executor_unavailable
from tracecat.agent.executor.activity import AgentExecutorResult


class _PreviousClassification(BaseModel):
    """The strict v1 wire shape, pinned to an existing failure kind."""

    model_config = ConfigDict(extra="forbid")

    schema_: Literal["tracecat.error.v1"] = Field(alias="schema")
    owner: Literal["platform"]
    kind: Literal["agent.executor.unavailable"]
    message: str
    retry_disposition: Literal["retryable"]
    cause_type: str | None = None


class _PreviousExecutorResult(BaseModel):
    """Older activity-result readers ignore additive top-level fields."""

    success: bool
    classification: _PreviousClassification | None = None


def test_agent_diagnostics_do_not_change_the_v1_classification_wire_shape() -> None:
    classification = agent_executor_unavailable()
    result = AgentExecutorResult(
        success=False,
        classification=classification,
        diagnostic=LLMErrorDiagnostics(
            route="managed", provider_configuration="custom"
        ),
    )
    serialized = result.model_dump_json(exclude_unset=True)

    restored = AgentExecutorResult.model_validate_json(serialized)
    previous = _PreviousExecutorResult.model_validate_json(serialized)

    assert restored == result
    assert previous.classification is not None
    assert previous.classification.model_dump(
        by_alias=True
    ) == classification.model_dump(mode="json")
    assert "diagnostic" not in AgentExecutorResult(success=True).model_dump(mode="json")


def test_agent_diagnostics_reject_sensitive_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LLMErrorDiagnostics.model_validate(
            {
                "route": "managed",
                "provider_configuration": "custom",
                "url": "https://synthetic.example",
            }
        )
    with pytest.raises(ValidationError):
        LLMErrorDiagnostics.model_validate(
            {"route": "managed", "provider_configuration": "synthetic-display-name"}
        )
