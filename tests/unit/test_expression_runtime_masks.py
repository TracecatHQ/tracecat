"""Observed intermediate values mask diagnostics without hiding unrelated prose."""

import asyncio
import base64
from collections.abc import AsyncIterator, Iterator
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
from tracecat.expressions.policy import build_provenance
from tracecat.secrets.error_masking import ErrorMaskingMode
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


@pytest.fixture
async def async_masks(masks: SecretMaskCollector) -> AsyncIterator[SecretMaskCollector]:
    """Bind masks inside AnyIO's task even when its runner already exists."""
    token = ctx_secret_masks.set(masks)
    try:
        yield masks
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
    assert "ValueError in int(***)" in str(error)
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
    "secret",
    [
        "x" * 198,
        "x" * 199,
        "synthetic-private-credential-" + "abcdef0123456789" * 30,
        "escaped\n\\\"'" * 40,
        "é漢字" * 100,
        b"synthetic-private-credential-" * 20,
    ],
)
@pytest.mark.parametrize(
    "expression", ["int(SECRETS.api.TOKEN)", "SECRETS.api.TOKEN -> int"]
)
def test_truncated_integer_errors_mask_observed_secret(
    masks: SecretMaskCollector, secret: str | bytes, expression: str
) -> None:
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ " + expression + " }}",
            operand={"SECRETS": {"api": {"TOKEN": secret}}},
        )

    for diagnostic in (str(caught.value), str(caught.value.detail)):
        assert diagnostic.endswith("ValueError in int(***)")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    info = ExecutorActionErrorInfo.from_exc(caught.value, action_name="testing.probe")
    assert service._sanitize_error_info(info, None).message == info.message


def test_truncated_integer_error_masks_transformed_secret(
    masks: SecretMaskCollector,
) -> None:
    secret = "synthetic-private-credential-" * 20
    encoded = base64.b64encode(secret.encode()).decode()
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(FN.to_base64(SECRETS.api.TOKEN)) }}",
            operand={"SECRETS": {"api": {"TOKEN": secret}}},
        )

    assert encoded in masks.values
    assert str(caught.value).endswith("ValueError in int(***)")
    assert str(caught.value.detail).endswith("ValueError in int(***)")


def test_long_public_integer_error_preserves_diagnostic(
    masks: SecretMaskCollector,
) -> None:
    masks.observe("unrelated-private-token" * 20)
    public = "public-non-numeric-value-" * 20
    with pytest.raises(ValueError) as original:
        int(public)
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(TRIGGER.value) }}", operand={"TRIGGER": {"value": public}}
        )
    assert str(original.value) in str(caught.value)
    assert str(original.value) in str(caught.value.detail)


@pytest.mark.parametrize(
    ("operation", "argument", "fragment"),
    [
        (
            'FN.parse_datetime(VALUE, "%Y-%m-%d")',
            "2026-09-25-synthetic-credential-value",
            "synthetic-credential-value",
        ),
        (
            'FN.regex_match(VALUE, "a")',
            "(?P<synthetic-credential-value>a)",
            "synthetic-credential-value",
        ),
        (
            "FN.deserialize_yaml(VALUE)",
            "public: ok\n  synthetic-credential-value: [\n",
            "synthetic-credential-value",
        ),
    ],
)
@pytest.mark.parametrize("secret", [False, True])
def test_partial_operand_diagnostics_are_safe_only_when_needed(
    masks: SecretMaskCollector,
    operation: str,
    argument: str,
    fragment: str,
    secret: bool,
) -> None:
    reference = "SECRETS.api.TOKEN" if secret else "TRIGGER.value"
    expression = operation.replace("VALUE", reference)
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ " + expression + " }}",
            operand=(
                {"SECRETS": {"api": {"TOKEN": argument}}}
                if secret
                else {"TRIGGER": {"value": argument}}
            ),
        )
    for diagnostic in (str(caught.value), str(caught.value.detail)):
        if secret:
            assert fragment not in diagnostic
            assert operation.split("(")[0] + "(***)" in diagnostic
        else:
            assert fragment in diagnostic
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "context", ["ACTIONS", "steps", "var", "TRIGGER", "VARS", "ENV"]
)
def test_known_secret_fragments_are_protected_in_runtime_carriers(
    masks: SecretMaskCollector, context: str
) -> None:
    secret = "2026-09-25-synthetic-credential-value"
    masks.observe(secret)
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ FN.parse_datetime(" + context + '.value, "%Y-%m-%d") }}',
            operand={context: {"value": secret}},
        )
    assert "synthetic-credential-value" not in str(caught.value)
    assert "synthetic-credential-value" not in str(caught.value.detail)
    assert "ValueError in FN.parse_datetime(***)" in str(caught.value)


def test_public_failure_keeps_diagnostic_after_secret_sibling_evaluation(
    masks: SecretMaskCollector,
) -> None:
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            '${{ [SECRETS.api.TOKEN, int("public-invalid-number")] }}',
            operand={"SECRETS": {"api": {"TOKEN": SECRET}}},
        )
    assert "invalid literal for int()" in str(caught.value)
    assert "public-invalid-number" in str(caught.value.detail)


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
            provenance=provenance,
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
    monkeypatch: pytest.MonkeyPatch, async_masks: SecretMaskCollector
) -> None:
    masks = async_masks
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


@pytest.mark.parametrize(
    "reference",
    [
        "steps.fetch.result.status",
        "(steps.fetch.result)['status']",
        "(steps.fetch.result)['nested']['status']",
        "(steps.fetch.result)['items'][-1]['status']",
    ],
)
def test_selecting_public_value_does_not_inherit_parent_key_sensitivity(
    masks: SecretMaskCollector, reference: str
) -> None:
    masks.observe(SECRET)
    public = "not-a-number"
    encoded = base64.b64encode(public.encode()).decode()
    value = {
        SECRET: "public-value",
        "status": public,
        "nested": {"status": public},
        "items": [{"status": public}],
    }
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(FN.to_base64(" + reference + ")) }}",
            operand={"steps": {"fetch": {"result": value}}},
        )
    assert encoded in str(caught.value)
    assert encoded in str(caught.value.detail)
    assert public not in masks.values
    assert encoded not in masks.values


@pytest.mark.parametrize(
    "reference",
    [
        "inputs.payload.status",
        "inputs.payload['status']",
        "(inputs.payload)['status']",
        "inputs.payload.nested.status",
        "inputs.payload.items[-1].status",
    ],
)
def test_template_input_selection_ignores_ancestor_key_sensitivity(
    masks: SecretMaskCollector, reference: str
) -> None:
    masks.observe(SECRET)
    public = "not-a-number"
    encoded = base64.b64encode(public.encode()).decode()
    source = {
        "${{ SECRETS.api.TOKEN }}": "public-value",
        "status": public,
        "nested": {"status": public},
        "items": [{"status": public}],
    }
    runtime = {**source, SECRET: source["${{ SECRETS.api.TOKEN }}"]}
    del runtime["${{ SECRETS.api.TOKEN }}"]
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(FN.to_base64(" + reference + ")) }}",
            operand={"inputs": {"payload": runtime}},
            provenance=build_provenance({"payload": source}),
        )
    assert encoded in str(caught.value)
    assert encoded in str(caught.value.detail)
    assert public not in masks.values
    assert encoded not in masks.values


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("container", [False, True])
def test_public_projection_remains_public_across_nested_templates(
    masks: SecretMaskCollector, depth: int, container: bool
) -> None:
    masks.observe(SECRET)
    public = {"status": "not-a-number"} if container else "not-a-number"
    provenance = build_provenance(
        {"payload": {"${{ SECRETS.api.TOKEN }}": "public", "value": public}}
    )
    source = "${{ inputs.payload.value }}"
    for _ in range(depth):
        provenance = build_provenance({"child": source}, provenance)
        source = "${{ inputs.child }}"
    value_expression = (
        "FN.serialize_json(inputs.child)" if container else "inputs.child"
    )
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(" + value_expression + ") }}",
            operand={"inputs": {"child": public}},
            provenance=provenance,
        )
    assert "not-a-number" in str(caught.value)
    assert "not-a-number" in str(caught.value.detail)
    assert "not-a-number" not in masks.values


@pytest.mark.parametrize(
    "reference",
    [
        "(steps.fetch.result)['token']",
        "FN.to_keys((steps.fetch.result)['nested'])[0]",
        "FN.deserialize_json(SECRETS.api.TOKEN)['token']",
    ],
)
def test_selecting_secret_value_or_nested_secret_keys_retains_sensitivity(
    masks: SecretMaskCollector, reference: str
) -> None:
    masks.observe(SECRET)
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(FN.to_base64(" + reference + ")) }}",
            operand={
                "steps": {
                    "fetch": {"result": {"token": SECRET, "nested": {SECRET: "public"}}}
                },
                "SECRETS": {"api": {"TOKEN": '{"token":"' + SECRET + '"}'}},
            },
        )
    assert ENCODED in masks.values
    assert ENCODED not in str(caught.value)
    assert ENCODED not in str(caught.value.detail)
    assert "ValueError in int(***)" in str(caught.value)


@pytest.mark.parametrize(
    "transform",
    ["FN.to_keys(steps.fetch.result)[0]", "FN.serialize_json(steps.fetch.result)"],
)
def test_mapping_key_transformations_are_masked(
    masks: SecretMaskCollector, transform: str
) -> None:
    masks.observe(SECRET)
    value = {SECRET: "public-value"}
    encoded = eval_templated_object(
        "${{ FN.to_base64(" + transform + ") }}",
        operand={"steps": {"fetch": {"result": value}}},
    )
    assert encoded in masks.values
    masks.values.clear()
    masks.observe(SECRET)

    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(FN.to_base64(" + transform + ")) }}",
            operand={"steps": {"fetch": {"result": value}}},
        )

    assert "ValueError in int(***)" in str(caught.value)
    assert encoded not in str(caught.value)
    assert encoded not in str(caught.value.detail)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("nested", [False, True])
def test_decoded_secret_mapping_keys_are_masked(
    masks: SecretMaskCollector, nested: bool
) -> None:
    secret_json = '{"synthetic-key-fragment":"public-value"}'
    if nested:
        secret_json = '{"payload":[' + secret_json + "]}"
    decoded = "FN.deserialize_json(SECRETS.api.TOKEN)"
    if nested:
        decoded += "['payload'][0]"

    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ FN.map_keys(" + decoded + ', {"dummy": "dummy"}) }}',
            operand={"SECRETS": {"api": {"TOKEN": secret_json}}},
        )

    assert "ValueError in FN.map_keys(***)" in str(caught.value)
    assert "synthetic-key-fragment" not in str(caught.value)
    assert "synthetic-key-fragment" not in str(caught.value.detail)
    assert "api" not in masks.values
    assert "TOKEN" not in masks.values
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_reading_secret_mapping_does_not_collect_field_names(
    masks: SecretMaskCollector,
) -> None:
    eval_templated_object(
        "${{ SECRETS.api }}", operand={"SECRETS": {"api": {"TOKEN": SECRET}}}
    )
    assert SECRET in masks.values
    assert "api" not in masks.values
    assert "TOKEN" not in masks.values


@pytest.fixture(autouse=True)
def provenance_error_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executor tests run with the default mode without accessing settings storage."""
    monkeypatch.setattr(
        "tracecat.executor.service.get_error_masking_mode",
        AsyncMock(return_value=ErrorMaskingMode.PROVENANCE),
    )
