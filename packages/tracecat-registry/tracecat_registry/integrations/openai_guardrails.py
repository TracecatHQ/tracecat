"""Run OpenAI Guardrails checks as a Tracecat UDF."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, TypeVar, TypedDict, cast

from guardrails.checks.text.jailbreak import jailbreak
from guardrails.checks.text.llm_base import LLMConfig
from guardrails.checks.text.moderation import ModerationCfg, moderation
from guardrails.checks.text.nsfw import nsfw_content
from guardrails.checks.text.pii import PIIConfig, pii
from guardrails.types import GuardrailLLMContextProto, GuardrailResult
from openai import AsyncOpenAI
from pydantic import BaseModel
from typing_extensions import Doc, NotRequired

from tracecat_registry import RegistrySecret, registry, secrets


openai_guardrails_secret = RegistrySecret(
    name="openai", optional_keys=["OPENAI_API_KEY"]
)
"""OpenAI API key.

- name: `openai`
- optional_keys:
    - `OPENAI_API_KEY`
"""


MaybeAwaitableGuardrail = GuardrailResult | Awaitable[GuardrailResult]


class GuardrailCheckResult(TypedDict):
    """Result from a single guardrail check."""

    guardrail_name: str
    tripwire_triggered: bool
    execution_failed: bool
    info: dict[str, Any]
    error: NotRequired[str]


class GuardrailSummary(TypedDict):
    """Summary of all guardrail checks."""

    total_checks: int
    tripwires_triggered: list[str]
    failed_checks: list[str]


class GuardrailCheckAllResult(TypedDict):
    """Complete result from running all guardrail checks."""

    prompt: str
    summary: GuardrailSummary
    results: list[GuardrailCheckResult]


@dataclass(slots=True)
class _GuardrailContext(GuardrailLLMContextProto):
    guardrail_llm: AsyncOpenAI

    def get_conversation_history(self) -> list | None:
        return None


@dataclass(slots=True)
class _GuardrailCheckDefinition:
    name: str
    run: Callable[[GuardrailLLMContextProto, str], MaybeAwaitableGuardrail]


CfgT = TypeVar("CfgT", bound=BaseModel)


def _make_guardrail_definition(
    name: str,
    func: Callable[[GuardrailLLMContextProto, str, CfgT], MaybeAwaitableGuardrail],
    config: CfgT,
) -> _GuardrailCheckDefinition:
    def _runner(
        ctx: GuardrailLLMContextProto,
        text: str,
        *,
        _func=func,
        _config=config,
    ) -> MaybeAwaitableGuardrail:
        return _func(ctx, text, _config.model_copy(deep=True))

    return _GuardrailCheckDefinition(name=name, run=_runner)


def _to_serializable(value: Any) -> Any:
    """Convert guardrail metadata into JSON-serializable structures."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return _to_serializable(value.model_dump())
    if isinstance(value, dict):
        return {k: _to_serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_serializable(v) for v in value]
    return value


async def _ensure_guardrail_result(
    result: MaybeAwaitableGuardrail,
) -> GuardrailResult:
    if inspect.isawaitable(result):
        return await cast(Awaitable[GuardrailResult], result)
    return cast(GuardrailResult, result)


async def _run_guardrail_check(
    ctx: _GuardrailContext,
    prompt: str,
    definition: _GuardrailCheckDefinition,
) -> GuardrailCheckResult:
    """Execute a guardrail definition and normalize the result structure."""
    try:
        result = await _ensure_guardrail_result(definition.run(ctx, prompt))
        payload: GuardrailCheckResult = {
            "guardrail_name": definition.name,
            "tripwire_triggered": result.tripwire_triggered,
            "execution_failed": result.execution_failed,
            "info": _to_serializable(result.info),
        }
        if result.original_exception is not None:
            payload["error"] = str(result.original_exception)
        return payload
    except Exception as exc:  # pragma: no cover - guardrail runtime errors
        return {
            "guardrail_name": definition.name,
            "tripwire_triggered": False,
            "execution_failed": True,
            "error": str(exc),
            "info": {},
        }


async def _run_all_guardrails(
    ctx: _GuardrailContext,
    prompt: str,
    definitions: list[_GuardrailCheckDefinition],
) -> list[GuardrailCheckResult]:
    tasks = [
        _run_guardrail_check(ctx, prompt, definition) for definition in definitions
    ]
    return await asyncio.gather(*tasks)


@registry.register(
    default_title="Run OpenAI Guardrails",
    description="Run OpenAI Guardrails' moderation, jailbreak, NSFW, and PII checks against a prompt.",
    display_group="OpenAI Guardrails",
    doc_url="https://github.com/openai/openai-guardrails-python",
    namespace="ai.guardrails",
    secrets=[openai_guardrails_secret],
)
async def check_all(
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
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    ctx = _GuardrailContext(guardrail_llm=client)

    common_llm_kwargs = {
        "model": model_name,
        "confidence_threshold": confidence_threshold,
    }

    guardrail_checks = [
        _make_guardrail_definition("Moderation", moderation, ModerationCfg()),
        _make_guardrail_definition(
            "Contains PII",
            pii,
            PIIConfig(
                block=True,
                detect_encoded_pii=False,
            ),
        ),
        _make_guardrail_definition(
            "Jailbreak",
            jailbreak,
            LLMConfig(**common_llm_kwargs),
        ),
        _make_guardrail_definition(
            "NSFW Text",
            nsfw_content,
            LLMConfig(**common_llm_kwargs),
        ),
    ]

    try:
        results = await _run_all_guardrails(ctx, prompt, guardrail_checks)
    finally:
        await client.close()

    tripwires_triggered = [
        r["guardrail_name"] for r in results if r["tripwire_triggered"]
    ]
    execution_failed = [r["guardrail_name"] for r in results if r["execution_failed"]]

    return {
        "prompt": prompt,
        "summary": {
            "total_checks": len(guardrail_checks),
            "tripwires_triggered": tripwires_triggered,
            "failed_checks": execution_failed,
        },
        "results": results,
    }
