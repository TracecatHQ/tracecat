"""Tests for OpenAI Guardrails integration."""

import json

import pytest
from tracecat_registry.integrations.openai_guardrails import check_all

requires_openai_mocks = pytest.mark.usefixtures("mock_openai_secrets")


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_safe_prompt():
    """Test guardrails with a safe, normal prompt."""
    prompt = "Hello, how are you today? I'm looking for help with a technical question."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-4o-mini",
        confidence_threshold=0.7,
    )

    # Verify structure
    assert isinstance(result, dict)
    assert "prompt" in result
    assert "results" in result
    assert "tripwires_triggered" in result
    assert "execution_failed" in result

    # Verify prompt matches input
    assert result["prompt"] == prompt

    # Verify results is a list with expected checks
    assert isinstance(result["results"], list)
    assert len(result["results"]) == 4  # moderation, PII, jailbreak, NSFW

    # Verify each result has expected structure
    for check_result in result["results"]:
        assert isinstance(check_result, dict)
        assert "tripwire_triggered" in check_result
        assert "execution_failed" in check_result
        assert isinstance(check_result["tripwire_triggered"], bool)
        assert isinstance(check_result["execution_failed"], bool)

    # Verify counts
    assert isinstance(result["tripwires_triggered"], int)
    assert isinstance(result["execution_failed"], int)
    assert result["tripwires_triggered"] >= 0
    assert result["execution_failed"] >= 0

    # Verify JSON serializability
    json_str = json.dumps(result)
    assert isinstance(json_str, str)
    # Verify we can parse it back
    parsed = json.loads(json_str)
    assert parsed == result


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_with_pii():
    """Test guardrails with a prompt containing potential PII."""
    prompt = "My email is john.doe@example.com and my phone number is 555-1234."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-4o-mini",
        confidence_threshold=0.7,
    )

    # Verify structure
    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert len(result["results"]) == 4

    # Verify JSON serializability
    json_str = json.dumps(result)
    parsed = json.loads(json_str)
    assert parsed == result


@pytest.mark.anyio
@requires_openai_mocks
async def test_check_all_custom_confidence_threshold():
    """Test guardrails with a custom confidence threshold."""
    prompt = "This is a test prompt."

    result = await check_all(
        prompt=prompt,
        model_name="gpt-4o-mini",
        confidence_threshold=0.5,
    )

    # Verify structure
    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert len(result["results"]) == 4

    # Verify JSON serializability
    json_str = json.dumps(result)
    parsed = json.loads(json_str)
    assert parsed == result
