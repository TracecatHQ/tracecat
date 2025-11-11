"""Run OpenAI Guardrails checks as a Tracecat UDF."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, TypeVar, cast

from guardrails.checks.text.jailbreak import jailbreak
from guardrails.checks.text.llm_base import LLMConfig
from guardrails.checks.text.moderation import ModerationCfg, moderation
from guardrails.checks.text.nsfw import nsfw_content
from guardrails.checks.text.pii import PIIConfig, pii
from guardrails.types import GuardrailLLMContextProto, GuardrailResult
from openai import AsyncOpenAI
from pydantic import BaseModel
from typing_extensions import Doc

from tracecat.registry.fields import TextArea
from tracecat_registry import RegistrySecret, registry, secrets


openai_guardrails_secret = RegistrySecret(name="openai", keys=["OPENAI_API_KEY"])
"""OpenAI API key.

- name: `openai`
- keys:
    - `OPENAI_API_KEY`
"""


MaybeAwaitableGuardrail = GuardrailResult | Awaitable[GuardrailResult]


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
) -> dict[str, Any]:
    """Execute a guardrail definition and normalize the result structure."""
    try:
        result = await _ensure_guardrail_result(definition.run(ctx, prompt))
        payload: dict[str, Any] = {
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
) -> list[dict[str, Any]]:
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
        TextArea(
            rows=6, placeholder="Paste the text to evaluate with OpenAI Guardrails."
        ),
    ],
) -> dict[str, Any]:
    """Run a curated set of OpenAI Guardrails checks with OpenAI providers."""
    api_key = secrets.get("OPENAI_API_KEY")
    client = AsyncOpenAI(api_key=api_key)
    ctx = _GuardrailContext(guardrail_llm=client)

    common_llm_kwargs = {
        "model": "gpt-4o-mini",
        "confidence_threshold": 0.7,
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

    tripwire_hits = [r["guardrail_name"] for r in results if r["tripwire_triggered"]]
    failed_checks = [r["guardrail_name"] for r in results if r["execution_failed"]]

    return {
        "prompt": prompt,
        "summary": {
            "total_checks": len(guardrail_checks),
            "tripwires_triggered": tripwire_hits,
            "failed_checks": failed_checks,
        },
        "results": results,
    }
