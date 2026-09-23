"""Observed intermediate values mask diagnostics without hiding unrelated prose."""

import asyncio
import base64
from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import pytest

from tracecat.contexts import ctx_secret_masks
from tracecat.exceptions import ExecutionError, TracecatExpressionError
from tracecat.executor import service
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ExecutorResultFailure,
    ResolvedContext,
)
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.policy import TaintState, build_provenance
from tracecat.secrets.masking import SecretMaskCollector

SECRET = "synthetic-credential-value"
ENCODED = base64.b64encode(SECRET.encode()).decode()


@pytest.fixture
def masks() -> Iterator[SecretMaskCollector]:
    collector = SecretMaskCollector()
    token = ctx_secret_masks.set(collector)
    try:
        yield collector
    finally:
        ctx_secret_masks.reset(token)


@pytest.mark.parametrize(
    "expression",
    [
        "int(FN.to_base64(SECRETS.api.TOKEN))",
        "FN.to_base64(SECRETS.api.TOKEN) -> int",
        "int('Bearer ' + FN.to_base64(SECRETS.api.TOKEN))",
    ],
)
def test_failed_parent_keeps_diagnostic_and_masks_intermediate(
    masks: SecretMaskCollector, expression: str
) -> None:
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ " + expression + " }}",
            operand={"SECRETS": {"api": {"TOKEN": SECRET}}},
        )

    assert ENCODED in masks.values
    error = caught.value
    assert "invalid literal for int()" in str(error)
    assert "***" in str(error)
    assert "Details withheld" not in str(error)
    assert SECRET not in str(error)
    assert ENCODED not in str(error)
    assert ENCODED not in str(error.detail)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "api" not in masks.values
    assert "TOKEN" not in masks.values


@pytest.mark.parametrize(
    "secret", ["first\nsecond", "back\\slash", "both'\"quotes", "é漢字", "x"]
)
def test_diagnostic_representations_are_masked(secret: str) -> None:
    masks = SecretMaskCollector()
    masks.observe(secret)
    assert secret not in masks.redact(f"Rejected {secret!r}")
    assert (
        masks.redact(f"Rejected {secret!r}") == "Rejected '***'"
        or masks.redact(f"Rejected {secret!r}") == 'Rejected "***"'
    )
    assert masks.redact({secret: {"reason": secret}}) == {"***": {"reason": "***"}}


@pytest.mark.parametrize(
    "reference",
    [
        "inputs.payload.public",
        "(inputs.payload)['public']",
        "[inputs.payload.token, inputs.payload.public][1]",
    ],
)
def test_public_sibling_does_not_inherit_secret_dependency(
    masks: SecretMaskCollector, reference: str
) -> None:
    provenance = build_provenance(
        {"payload": {"token": "${{ SECRETS.api.TOKEN }}", "public": "not-a-number"}}
    )
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(" + reference + ") }}",
            operand={
                "inputs": {"payload": {"token": SECRET, "public": "not-a-number"}}
            },
            taint=TaintState(provenance=provenance),
        )
    assert "not-a-number" in str(caught.value)
    assert "not-a-number" not in masks.values


def test_skipped_branch_is_not_evaluated_or_collected(
    masks: SecretMaskCollector,
) -> None:
    result = eval_templated_object(
        "${{ 'public' if True else FN.to_base64(SECRETS.api.TOKEN) }}",
        operand={"SECRETS": {"api": {"TOKEN": SECRET}}},
    )
    assert result == "public"
    assert ENCODED not in masks.values
    assert "public" not in masks.values


def test_known_runtime_carrier_transformation_is_collected(
    masks: SecretMaskCollector,
) -> None:
    masks.observe(SECRET)
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(FN.to_base64(steps.fetch.result.token)) }}",
            operand={
                "steps": {"fetch": {"result": {"token": SECRET, "status": "public"}}}
            },
        )
    assert ENCODED in masks.values
    assert ENCODED not in str(caught.value)
    assert "public" not in masks.values


def test_unknown_runtime_carrier_retains_diagnostic(masks: SecretMaskCollector) -> None:
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(ACTIONS.fetch.result) }}",
            operand={"ACTIONS": {"fetch": {"result": "not-a-number"}}},
        )
    assert "not-a-number" in str(caught.value)
    assert not masks.values


@pytest.mark.anyio
async def test_invocations_isolate_masks_and_restore_parent(
    monkeypatch: pytest.MonkeyPatch, masks: SecretMaskCollector
) -> None:
    barrier = asyncio.Event()
    arrived = 0
    secrets = ("first-private-token", "second-private-token")

    async def prepare(action_input, role):
        own = action_input.task.args["own"]
        collector = ctx_secret_masks.get()
        assert collector is not None
        collector.observe(own)
        return service.PreparedContext(
            resolved_context=ResolvedContext(
                secrets={},
                evaluated_args={},
                action_impl=ActionImplementation(
                    type="udf", action_name="testing.probe"
                ),
                workspace_id="synthetic",
                workflow_id="synthetic",
                run_id="synthetic",
                executor_token="synthetic",
            ),
            mask_values=set(),
        )

    async def execute(**kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            barrier.set()
        await barrier.wait()
        return ExecutorResultFailure(
            error=ExecutorActionErrorInfo(
                action_name="testing.probe",
                type="ValueError",
                filename="probe.py",
                function="run",
                message=f"rejected {secrets[0]} and {secrets[1]}",
            )
        )

    monkeypatch.setattr(service.registry_resolver, "prefetch_lock", AsyncMock())
    monkeypatch.setattr(service, "prepare_resolved_context", prepare)
    backend = Mock(execute=AsyncMock(side_effect=execute))

    async def invoke(own: str) -> str:
        with pytest.raises(ExecutionError) as caught:
            await service.invoke_once(
                backend,
                Mock(task=Mock(action="testing.probe", args={"own": own})),
                Mock(role=Mock(organization_id="synthetic")),
            )
        assert ctx_secret_masks.get() is masks
        return str(caught.value)

    first, second = await asyncio.gather(*(invoke(value) for value in secrets))
    assert secrets[0] not in first and secrets[1] in first
    assert secrets[1] not in second and secrets[0] in second
    assert not masks.values


@pytest.mark.anyio
@pytest.mark.parametrize("failure_site", ["prepare", "backend"])
async def test_invocation_preserves_derived_masks_on_failure(
    monkeypatch: pytest.MonkeyPatch, failure_site: str
) -> None:
    async def prepare(action_input, role):
        expression = "FN.to_base64(SECRETS.api.TOKEN)"
        if failure_site == "prepare":
            expression = f"int({expression})"
        value = eval_templated_object(
            "${{ " + expression + " }}",
            operand={"SECRETS": {"api": {"TOKEN": SECRET}}},
        )
        return service.PreparedContext(
            resolved_context=ResolvedContext(
                secrets={},
                evaluated_args={"value": value},
                action_impl=ActionImplementation(
                    type="udf", action_name="testing.probe"
                ),
                workspace_id="synthetic",
                workflow_id="synthetic",
                run_id="synthetic",
                executor_token="synthetic",
            ),
            mask_values={SECRET},
        )

    monkeypatch.setattr(service.registry_resolver, "prefetch_lock", AsyncMock())
    monkeypatch.setattr(service, "prepare_resolved_context", prepare)
    backend = Mock(execute=AsyncMock(side_effect=ValueError(f"rejected {ENCODED}")))
    with pytest.raises(ExecutionError) as caught:
        await service.invoke_once(
            backend,
            Mock(task=Mock(action="testing.probe")),
            Mock(role=Mock(organization_id="synthetic")),
        )
    assert ENCODED not in str(caught.value)
    assert SECRET not in str(caught.value)
    assert "***" in str(caught.value)
    assert "Details withheld" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert backend.execute.await_count == (failure_site == "backend")


def test_short_secret_does_not_rewrite_error_schema_keys(
    masks: SecretMaskCollector,
) -> None:
    masks.observe("e")
    info = ExecutorActionErrorInfo(
        action_name="testing.probe",
        type="ValueError",
        filename="probe.py",
        function="run",
        message="rejected e",
        loop_vars={"private-key": "e"},
    )
    sanitized = service._sanitize_error_info(info, None)
    assert "e" not in sanitized.message
    assert sanitized.loop_vars == {"privat***-k***y": "***"}
    assert "message" in sanitized.model_dump()


def test_redaction_is_idempotent_for_mask_token_fragments() -> None:
    masks = SecretMaskCollector()
    masks.observe("*")
    once = masks.redact("rejected '*'")
    assert once == "rejected '***'"
    assert masks.redact(once) == once


def test_sensitive_mapping_key_does_not_mask_public_keys(
    masks: SecretMaskCollector,
) -> None:
    masks.observe(SECRET)
    value = {SECRET: "public-value", "status": "public-status"}
    assert (
        eval_templated_object(
            "${{ steps.fetch.result }}", operand={"steps": {"fetch": {"result": value}}}
        )
        == value
    )
    assert "status" not in masks.values
    assert "public-value" not in masks.values
    assert "public-status" not in masks.values
