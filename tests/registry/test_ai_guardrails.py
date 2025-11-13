"""Tests for OpenAI Guardrails integration."""

from collections.abc import Callable

import pytest
from guardrails.types import GuardrailResult
from tracecat_registry.integrations.openai_guardrails import check_all

GuardrailOverride = dict[str, tuple[bool, bool]]


@pytest.fixture
def mock_guardrail_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[GuardrailOverride | None], None]:
    """Patch OpenAI client and guardrail check fns to return deterministic results."""

    def _build_result(
        guardrail_name: str,
        *,
        triggered: bool = False,
        failed: bool = False,
        prompt: str | None = None,
    ) -> GuardrailResult:
        return GuardrailResult(
            tripwire_triggered=triggered,
            execution_failed=failed,
            info={
                "guardrail_name": guardrail_name,
                "prompt": prompt,
            },
        )

    def _mock(overrides: GuardrailOverride | None = None) -> None:
        overrides = overrides or {}

        def _stub(guardrail_name: str):
            def _runner(client, prompt, config):
                triggered, failed = overrides.get(guardrail_name, (False, False))
                return _build_result(
                    guardrail_name,
                    triggered=triggered,
                    failed=failed,
                    prompt=prompt,
                )

            return _runner

        module_path = "tracecat_registry.integrations.openai_guardrails"
        monkeypatch.setattr(f"{module_path}.moderation", _stub("Moderation"))
        monkeypatch.setattr(f"{module_path}.pii", _stub("Contains PII"))
        monkeypatch.setattr(f"{module_path}.jailbreak", _stub("Jailbreak"))
        monkeypatch.setattr(f"{module_path}.nsfw_content", _stub("NSFW Text"))
        monkeypatch.setattr(
            f"{module_path}.secrets.get",
            lambda key: "test-openai-key" if key == "OPENAI_API_KEY" else None,
        )

        class DummyOpenAI:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(f"{module_path}.OpenAI", DummyOpenAI)

    return _mock


def test_check_all_basic_safe_prompt(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test guardrails with a safe, benign prompt."""
    mock_guardrail_checks()
    prompt = "Hello, how are you today? I hope you're having a great day."

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.7,
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert result["tripwires_triggered"] == 0
    assert result["execution_failed"] == 0

    guardrail_results = result["results"]
    assert isinstance(guardrail_results, list)
    assert len(guardrail_results) == 4
    assert all(isinstance(gr, GuardrailResult) for gr in guardrail_results)


def test_check_all_with_pii_prompt(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test guardrails with a prompt that might contain PII."""
    mock_guardrail_checks({"Contains PII": (True, False)})
    prompt = "My email is john.doe@example.com and my phone number is 555-1234."

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.7,
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert result["tripwires_triggered"] == 1

    pii_result = next(
        (r for r in result["results"] if r.info["guardrail_name"] == "Contains PII"),
        None,
    )
    assert pii_result is not None
    assert pii_result.tripwire_triggered is True
    assert pii_result.execution_failed is False


def test_check_all_with_custom_model(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test guardrails with a custom model name."""
    mock_guardrail_checks()
    prompt = "This is a test prompt."

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.8,
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert len(result["results"]) == 4


def test_check_all_with_custom_confidence_threshold(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test guardrails with a custom confidence threshold."""
    mock_guardrail_checks()
    prompt = "This is a test prompt."

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
        confidence_threshold=0.5,
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert len(result["results"]) == 4


def test_check_all_guardrail_names(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test that all expected guardrail names are present."""
    mock_guardrail_checks()
    prompt = "This is a test prompt."

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
    )

    guardrail_names = [r.info["guardrail_name"] for r in result["results"]]
    expected_names = ["Moderation", "Contains PII", "Jailbreak", "NSFW Text"]

    assert set(guardrail_names) == set(expected_names)


def test_check_all_empty_prompt(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test guardrails with an empty prompt."""
    mock_guardrail_checks()
    prompt = ""

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
    )

    assert isinstance(result, dict)
    assert result["prompt"] == ""
    assert len(result["results"]) == 4


def test_check_all_long_prompt(
    mock_guardrail_checks: Callable[[GuardrailOverride | None], None],
):
    """Test guardrails with a long prompt."""
    mock_guardrail_checks()
    prompt = "This is a very long prompt. " * 100

    result = check_all(
        prompt=prompt,
        model_name="gpt-5-nano-2025-08-07",
    )

    assert isinstance(result, dict)
    assert result["prompt"] == prompt
    assert len(result["results"]) == 4
