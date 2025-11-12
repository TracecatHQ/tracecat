"""Tests for OpenAI Guardrails integration."""

import pytest
from tracecat_registry.integrations.openai_guardrails import check_all

requires_openai_mocks = pytest.mark.usefixtures("mock_openai_secrets")


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_basic_safe_prompt():
    """Test guardrails with a safe, benign prompt."""
    prompt = "Hello, how are you today? I hope you're having a great day."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.7,
    )

    # Verify structure
    assert isinstance(result, dict)
    assert "prompt" in result
    assert "summary" in result
    assert "results" in result

    # Verify prompt is returned
    assert result["prompt"] == prompt

    # Verify summary structure
    summary = result["summary"]
    assert isinstance(summary, dict)
    assert "total_checks" in summary
    assert "tripwires_triggered" in summary
    assert "failed_checks" in summary
    assert summary["total_checks"] == 4  # Moderation, PII, Jailbreak, NSFW

    # Verify results structure
    results = result["results"]
    assert isinstance(results, list)
    assert len(results) == 4

    # Verify each guardrail result structure
    for guardrail_result in results:
        assert isinstance(guardrail_result, dict)
        assert "guardrail_name" in guardrail_result
        assert "tripwire_triggered" in guardrail_result
        assert "execution_failed" in guardrail_result
        assert "info" in guardrail_result
        assert isinstance(guardrail_result["guardrail_name"], str)
        assert isinstance(guardrail_result["tripwire_triggered"], bool)
        assert isinstance(guardrail_result["execution_failed"], bool)
        assert isinstance(guardrail_result["info"], dict)

    # For a safe prompt, tripwires should not be triggered
    # (though this depends on the actual guardrail behavior)
    assert isinstance(summary["tripwires_triggered"], list)
    assert isinstance(summary["failed_checks"], list)


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_with_pii_prompt():
    """Test guardrails with a prompt that might contain PII."""
    prompt = "My email is john.doe@example.com and my phone number is 555-1234."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.7,
    )

    # Verify structure
    assert isinstance(result, dict)
    assert result["prompt"] == prompt

    # Check if PII guardrail was triggered
    pii_result = next(
        (r for r in result["results"] if r["guardrail_name"] == "Contains PII"),
        None,
    )
    assert pii_result is not None
    assert isinstance(pii_result["tripwire_triggered"], bool)
    assert isinstance(pii_result["execution_failed"], bool)


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_with_custom_model():
    """Test guardrails with a custom model name."""
    prompt = "This is a test prompt."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.8,
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert result["summary"]["total_checks"] == 4


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_with_custom_confidence_threshold():
    """Test guardrails with a custom confidence threshold."""
    prompt = "This is a test prompt."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.5,
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert result["summary"]["total_checks"] == 4


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_guardrail_names():
    """Test that all expected guardrail names are present."""
    prompt = "This is a test prompt."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
    )

    guardrail_names = [r["guardrail_name"] for r in result["results"]]
    expected_names = ["Moderation", "Contains PII", "Jailbreak", "NSFW Text"]

    assert set(guardrail_names) == set(expected_names)


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_empty_prompt():
    """Test guardrails with an empty prompt."""
    prompt = ""

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
    )

    assert isinstance(result, dict)
    assert result["prompt"] == ""
    assert result["summary"]["total_checks"] == 4


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_long_prompt():
    """Test guardrails with a long prompt."""
    prompt = "This is a very long prompt. " * 100

    result = await check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert result["summary"]["total_checks"] == 4
    assert len(result["results"]) == 4
