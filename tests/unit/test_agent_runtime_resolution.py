"""Tests for the ``RuntimeResolution`` diagnostic metadata.

These pin down the cascade-source semantics that surface on
``AgentOutput.runtime_resolution`` so workflow authors can answer "did my
override take effect?" without reading source. They cover the model contract
and the pydantic-ai runtime's resolution builder without invoking an LLM.
"""

from __future__ import annotations

import pytest

from tracecat.agent.runtime.pydantic_ai.runtime import _build_pydantic_ai_resolution
from tracecat.agent.schemas import AgentOutput, RuntimeResolution


def test_agent_output_runtime_resolution_defaults_none() -> None:
    # Backward compatibility: existing callers that don't set the field get
    # ``None``, i.e. zero behaviour change.
    import uuid

    output = AgentOutput(output="hi", duration=0.1, session_id=uuid.uuid4())
    assert output.runtime_resolution is None


def test_resolution_rejects_negative_lengths() -> None:
    with pytest.raises(ValueError):
        RuntimeResolution(
            runtime="pydantic_ai",
            system_prompt_source="default",
            system_prompt_length=-1,
            system_prompt_append_count=0,
            allowed_tools_source="default",
        )


def test_pydantic_ai_resolution_no_instructions() -> None:
    res = _build_pydantic_ai_resolution(None)
    assert res.runtime == "pydantic_ai"
    assert res.system_prompt_source == "default"
    assert res.system_prompt_length == 0
    assert res.system_prompt_append_count == 0
    # pydantic-ai has no implicit built-in toolset, so this is always default.
    assert res.allowed_tools_source == "default"
    assert res.allowed_tools_count is None


def test_pydantic_ai_resolution_empty_string_is_default() -> None:
    # An empty instructions string is indistinguishable from "no override".
    res = _build_pydantic_ai_resolution("")
    assert res.system_prompt_source == "default"
    assert res.system_prompt_append_count == 0


def test_pydantic_ai_resolution_with_instructions() -> None:
    instructions = "You are a security analyst. Be thorough."
    res = _build_pydantic_ai_resolution(instructions)
    assert res.system_prompt_source == "action"
    assert res.system_prompt_length == len(instructions)
    assert res.system_prompt_append_count == 1
