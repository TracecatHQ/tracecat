"""Run OpenAI Guardrails checks as a Tracecat UDF."""

from __future__ import annotations

from typing import Any, Annotated, TypedDict

from guardrails.checks.text.jailbreak import jailbreak
from guardrails.checks.text.llm_base import LLMConfig
from guardrails.checks.text.moderation import ModerationCfg, moderation
from guardrails.checks.text.nsfw import nsfw_content
from guardrails.checks.text.pii import PIIConfig, pii
from openai import OpenAI
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets


openai_guardrails_secret = RegistrySecret(
    name="openai", optional_keys=["OPENAI_API_KEY"]
)
"""OpenAI API key.

- name: `openai`
- optional_keys:
    - `OPENAI_API_KEY`
"""


class GuardrailCheckAllResult(TypedDict):
    """Result from running all guardrail checks."""

    prompt: str
    results: list[dict[str, Any]]
    tripwires_triggered: int
    execution_failed: int


@registry.register(
    default_title="Run OpenAI Guardrails",
    description="Run OpenAI Guardrails' moderation, jailbreak, NSFW, and PII checks against a prompt.",
    display_group="OpenAI Guardrails",
    doc_url="https://github.com/openai/openai-guardrails-python",
    namespace="ai.guardrails",
    secrets=[openai_guardrails_secret],
)
def check_all(
    prompt: Annotated[
        str,
        Doc("Text prompt to validate."),
    ],
    model_name: Annotated[
        str,
        Doc("Name of the model to use."),
    ] = "gpt-5-mini-2025-08-07",
    base_url: Annotated[
        str | None,
        Doc("Base URL for the OpenAI API."),
    ] = None,
    confidence_threshold: Annotated[
        float,
        Doc("Confidence threshold for the guardrail checks."),
    ] = 0.7,
) -> GuardrailCheckAllResult:
    """Run a curated set of OpenAI Guardrails checks with OpenAI providers."""
    api_key = secrets.get("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key, base_url=base_url)

    common_llm_kwargs = {
        "model": model_name,
        "confidence_threshold": confidence_threshold,
    }

    # Run checks sequentially
    results: list[dict[str, Any]] = []
    tripwires_triggered = 0
    execution_failed = 0
    checks = [
        (moderation, ModerationCfg()),
        (
            pii,
            PIIConfig(
                block=True,
                detect_encoded_pii=False,
            ),
        ),
        (jailbreak, LLMConfig(**common_llm_kwargs)),
        (nsfw_content, LLMConfig(**common_llm_kwargs)),
    ]

    for func, config in checks:
        result = func(client, prompt, config.model_copy(deep=True))
        if result.tripwire_triggered:
            tripwires_triggered += 1
        if result.execution_failed:
            execution_failed += 1
        results.append(result.model_dump(mode="json"))

    return GuardrailCheckAllResult(
        prompt=prompt,
        results=results,
        tripwires_triggered=tripwires_triggered,
        execution_failed=execution_failed,
    )
