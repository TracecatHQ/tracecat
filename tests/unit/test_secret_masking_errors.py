"""Regression tests for secret text in execution errors.

Secret plaintext must not reach exception messages, error info payloads, or log
sinks when expression evaluation fails with a secret as the operand.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest

from tracecat.contexts import ctx_secret_masks
from tracecat.exceptions import TracecatExpressionError
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.executor.secret_preprocessors import collect_mask_values
from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.policy import build_provenance
from tracecat.secrets.common import (
    MaskedSecretError,
    await_with_masked_errors,
    call_with_masked_errors,
    mask_exception,
)
from tracecat.secrets.masking import SecretMaskCollector

CANARY = "sk-CANARY-7f3a91d4e6b2"


@pytest.fixture(autouse=True)
def mask_scope() -> Iterator[SecretMaskCollector]:
    masks = SecretMaskCollector()
    token = ctx_secret_masks.set(masks)
    try:
        yield masks
    finally:
        ctx_secret_masks.reset(token)


@pytest.fixture
def secrets() -> dict[str, dict[str, str]]:
    return {"svc": {"value": CANARY}}


@pytest.mark.parametrize(
    "expression",
    [
        "${{ int(SECRETS.svc.value) }}",
        # Trailing-typecast form must behave the same.
        "${{ SECRETS.svc.value -> int }}",
    ],
)
def test_arg_evaluation_error_masks_secret_value(
    expression: str, secrets: dict[str, dict[str, str]]
) -> None:
    context = {ExprContext.SECRETS: secrets}

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object({"value": expression}, operand=context)

    assert CANARY not in str(exc_info.value)
    assert CANARY not in str(getattr(exc_info.value, "detail", ""))
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_mask_exception_records_original_type(
    secrets: dict[str, dict[str, str]],
) -> None:
    masks = collect_mask_values([secrets])
    with pytest.raises(ValueError) as exc_info:
        int(CANARY)
    masked = mask_exception(exc_info.value, masks=masks)

    assert isinstance(masked, MaskedSecretError)
    assert masked.captured is not None
    assert masked.captured.original_type == "ValueError"
    assert masked.captured.filename.endswith(".py")
    assert masked.captured.lineno is not None
    assert masked.__traceback__ is None
    assert CANARY not in str(masked)


def test_evaluator_logging_omits_resolved_values(
    secrets: dict[str, dict[str, str]],
) -> None:
    from tracecat.logger import logger

    sink = io.StringIO()
    handler_id = logger.add(sink, level="TRACE", format="{message} | {extra}")
    try:
        with pytest.raises(TracecatExpressionError):
            eval_templated_object(
                {"value": "${{ int(SECRETS.svc.value) }}"},
                operand={ExprContext.SECRETS: secrets},
            )
    finally:
        logger.remove(handler_id)

    assert CANARY not in sink.getvalue()


def test_jsonpath_no_match_does_not_log_or_attach_operand(
    secrets: dict[str, dict[str, str]],
) -> None:
    """A strict jsonpath miss must not attach or log the operand.

    Operands may carry sensitive values, so the failure reports the expression
    only. The sole production strict caller is _select_input(), whose operand is
    authored source rather than resolved secrets, so this is data minimization
    rather than a secret-bearing operand on that path.
    """
    from tracecat.expressions.common import eval_jsonpath
    from tracecat.logger import logger

    operand: dict[str, Any] = {ExprContext.SECRETS: secrets}
    sink = io.StringIO()
    handler_id = logger.add(sink, level="ERROR", format="{message} | {extra}")
    try:
        with pytest.raises(TracecatExpressionError) as exc_info:
            eval_jsonpath("ACTIONS.missing.result", operand, strict=True)
    finally:
        logger.remove(handler_id)

    assert CANARY not in sink.getvalue()
    assert CANARY not in str(getattr(exc_info.value, "detail", ""))


@pytest.mark.parametrize(
    "secret_value",
    [
        "sk-CANARY-7f3a91d4e6b2",
        # repr() escaping defeats exact-string masking for these two: the secret
        # is present in the message but not spelled the same way.
        "line-one\nline-two",
        "back\\slash",
    ],
)
def test_selective_masking_survives_repr_escaping(secret_value: str) -> None:
    context = {ExprContext.SECRETS: {"svc": {"value": secret_value}}}

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"value": "${{ int(SECRETS.svc.value) }}"}, operand=context
        )

    message = str(exc_info.value)
    escaped = secret_value.encode("unicode_escape").decode()
    assert secret_value not in message
    assert escaped not in message


def test_untainted_expression_keeps_full_error() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"value": "${{ int(TRIGGER.count) }}"},
            operand={ExprContext.TRIGGER: {"count": "abc"}},
        )

    # The operand is not a secret, so the underlying error is reported in full.
    assert "invalid literal for int()" in str(exc_info.value)
    assert "abc" in str(exc_info.value)


def test_secret_expression_keeps_masked_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {ExprContext.SECRETS: {"svc": {"value": "not-a-number"}}}

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"value": "${{ int(SECRETS.svc.value) }}"}, operand=context
        )

    message = str(exc_info.value)
    assert "Details withheld:" not in message
    assert "invalid literal for int()" in message


def test_policy_errors_preserve_their_structured_diagnostic() -> None:
    from tracecat.expressions.policy import _CollectionPolicy

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"${{ SECRETS.api.KEY }}": "value"},
            policy=_CollectionPolicy(),
            key_policy=_CollectionPolicy(reject=True),
        )

    assert "Details withheld:" not in str(exc_info.value)
    assert exc_info.value.detail == {"code": "secret_expression_in_key"}


@pytest.mark.anyio
async def test_registry_jsonpath_miss_omits_runtime_operand() -> None:
    import logging as stdlib_logging

    from tracecat_registry.core.transform import deduplicate

    secret = "sk_live_CANARY_9f3a91d4e6b2"
    items = [{"id": 1, "auth_header": f"Bearer {secret}"}]

    stream = io.StringIO()
    handler = stdlib_logging.StreamHandler(stream)
    handler.setLevel(stdlib_logging.ERROR)
    root = stdlib_logging.getLogger()
    root.addHandler(handler)
    try:
        with pytest.raises(Exception) as exc_info:  # noqa: B017
            await deduplicate(items=items, keys=["nonexistent_key"])
    finally:
        root.removeHandler(handler)

    assert secret not in stream.getvalue()
    assert secret not in str(getattr(exc_info.value, "detail", ""))


@pytest.mark.parametrize(
    "expression",
    [
        "${{ int(inputs.value) }}",
        "${{ inputs.value -> int }}",
    ],
)
def test_known_template_input_expressions_are_masked(expression: str) -> None:
    secret = "line-one\nline-two-CANARY"
    context = {ExprContext.TEMPLATE_ACTION_INPUTS: {"value": secret}}

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"value": expression},
            operand=context,
            provenance=build_provenance({"value": "${{ SECRETS.svc.value }}"}),
        )

    message = str(exc_info.value)
    assert secret not in message
    assert secret.encode("unicode_escape").decode() not in message


def _walk_exception_chain(exc: BaseException) -> list[BaseException]:
    """Every exception reachable from `exc`, ignoring __suppress_context__.

    A renderer is free not to honour suppression, so the guarantee under test is
    that the plaintext is absent from the graph, not merely flagged as hidden.
    """
    seen: list[BaseException] = []
    todo: list[BaseException] = [exc]
    while todo and len(seen) < 16:
        cur = todo.pop()
        if any(cur is s for s in seen):
            continue
        seen.append(cur)
        todo.extend(link for link in (cur.__cause__, cur.__context__) if link)
    return seen


def test_call_with_masked_errors_severs_context() -> None:
    """The boundary helper owns the capture-then-raise contract: the masked
    copy is raised after its handler has exited, so nothing is attached."""
    with pytest.raises(MaskedSecretError) as exc_info:
        call_with_masked_errors(lambda: int(CANARY), masks={CANARY})

    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    for link in _walk_exception_chain(exc_info.value):
        assert CANARY not in str(link)
    info = ExecutorActionErrorInfo.from_exc(exc_info.value, action_name="a")
    assert info.type == "ValueError"
    assert info.function == "<lambda>"
    assert info.lineno is not None


@pytest.mark.anyio
async def test_await_with_masked_errors_severs_context() -> None:
    async def boom() -> None:
        int(CANARY)

    with pytest.raises(MaskedSecretError) as exc_info:
        await await_with_masked_errors(boom(), masks={CANARY})

    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    for link in _walk_exception_chain(exc_info.value):
        assert CANARY not in str(link)


@pytest.mark.parametrize("module_path", ["tracecat.observability.sentry"])
def test_workers_disable_sentry_local_variable_capture(module_path: str) -> None:
    """Sentry captures frame locals by default, and those frames hold secrets.

    A sanitized exception has a clean message, but `raise` builds a new traceback
    rooted at the raising frame, whose locals still hold the resolved `secrets`
    dict. Name-based scrubbing cannot cover it — secrets also live under names
    like `evaled_args` — so collection has to be disabled at init.
    """
    import ast
    import importlib
    from pathlib import Path

    module = importlib.import_module(module_path)
    source = Path(str(module.__file__)).read_text()
    tree = ast.parse(source)

    init_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "init"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sentry_sdk"
    ]
    assert init_calls, f"no sentry_sdk.init() found in {module_path}"

    for call in init_calls:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        assert "include_local_variables" in kwargs, (
            f"{module_path}: sentry_sdk.init() must disable local variable capture"
        )
        value = kwargs["include_local_variables"]
        assert isinstance(value, ast.Constant) and value.value is False


def test_from_exc_does_not_duck_type_unrelated_exceptions() -> None:
    """Only a MaskedSecretError carries a captured location.

    `filename` is a real attribute on some stdlib exceptions (OSError,
    SyntaxError), so probing for it with getattr() would misread them as
    carrying a captured failure. The check is by type, not by attribute name.
    """
    err = OSError("boom")
    err.filename = "/etc/passwd"

    info = ExecutorActionErrorInfo.from_exc(err, action_name="t")

    assert info.filename != "/etc/passwd"
    assert info.type == "OSError"


@pytest.mark.anyio
async def test_stdio_env_value_validation_does_not_echo_resolved_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keys are resolved before the string check, so a key can be plaintext."""
    from unittest import mock
    from uuid import UUID

    from tracecat.agent.preset import service as preset_module
    from tracecat.exceptions import TracecatValidationError

    monkeypatch.setattr(
        preset_module.secrets_manager,
        "get_action_secrets",
        mock.AsyncMock(return_value={"api": {"TOKEN": CANARY}}),
    )
    monkeypatch.setattr(
        preset_module, "get_workspace_variables", mock.AsyncMock(return_value={})
    )
    svc = preset_module.AgentPresetService.__new__(preset_module.AgentPresetService)
    svc.role = mock.Mock(workspace_id=UUID(int=1))  # type: ignore[attr-defined]

    with pytest.raises(TracecatValidationError) as exc_info:
        await svc.resolve_stdio_env(
            stdio_env={"${{ SECRETS.api.TOKEN }}": "${{ 1 }}"},
            mcp_integration_id=UUID(int=2),
            mcp_integration_slug="stub",
        )

    message = str(exc_info.value)
    assert CANARY not in message
    assert "invalid entries: [1]" in message


def test_mcp_env_validation_does_not_echo_the_key() -> None:
    """A resolved stdio env key can be secret plaintext.

    `resolve_stdio_env()` evaluates expressions in env KEYS, so a key authored as
    `${{ SECRETS.api.TOKEN }}` becomes plaintext before validation runs. That
    error reaches durable probe results and the API, and a resolved secret lands
    in this branch precisely because it is not a valid POSIX name.
    """
    from tracecat.integrations.mcp_validation import (
        MCPValidationError,
        validate_mcp_env,
    )

    with pytest.raises(MCPValidationError) as exc_info:
        validate_mcp_env({"GOOD": "value", f"{CANARY}-key": "value"})

    message = str(exc_info.value)
    assert CANARY not in message
    assert "entry 2" in message, "the entry must still be locatable by position"


WITHHELD_TEXT = "Details withheld: the expression may reference a secret."


async def _run_template(
    caller_args: dict[str, Any],
    steps: list[tuple[str, dict[str, Any]]],
    returns: Any,
    inputs: dict[str, Any],
    step_results: dict[str, Any] | None = None,
    steps_declaring_secrets: set[str] | None = None,
    failing_steps: set[str] | None = None,
) -> Any:
    """Drive canonical template orchestration with external IO stubbed."""
    from unittest import mock
    from uuid import UUID

    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import ActionStatement
    from tracecat.executor import service as service_module
    from tracecat.executor.schemas import (
        ActionImplementation,
        ExecutorResultFailure,
        ExecutorResultSuccess,
        ResolvedContext,
    )
    from tracecat.expressions.policy import build_provenance

    results = step_results or {}
    declaring = steps_declaring_secrets or set()
    failures = failing_steps or set()
    role = Role(
        type="service",
        organization_id=UUID(int=1),
        workspace_id=UUID(int=2),
        service_id="tracecat-executor",
    )
    run_input = mock.Mock(
        registry_lock=mock.sentinel.lock,
        exec_context={},
        task=ActionStatement(ref="caller", action="testing.stub", args=caller_args),
    )
    resolved = ResolvedContext(
        secrets={"runtime": {"TOKEN": CANARY}} if declaring else {},
        variables={},
        action_impl=ActionImplementation(
            type="template",
            action_name="testing.stub",
            template_definition={
                "name": "stub",
                "namespace": "testing",
                "title": "stub",
                "description": "secret masking regression",
                "display_group": "testing",
                "steps": [
                    {"ref": ref, "action": f"testing.{ref}", "args": args}
                    for ref, args in steps
                ],
                "returns": returns,
                "expects": {},
            },
        ),
        evaluated_args=inputs,
        workspace_id=str(role.workspace_id),
        workflow_id="00000000-0000-0000-0000-000000000003",
        run_id="00000000-0000-0000-0000-000000000004",
        executor_token="parent-token",
    )

    def load(action_name: str, *_args: Any) -> ActionImplementation:
        return ActionImplementation(type="udf", action_name=action_name)

    async def execute(
        **kwargs: Any,
    ) -> ExecutorResultSuccess | ExecutorResultFailure:
        action_name = kwargs["resolved_context"].action_impl.action_name
        ref = action_name.rsplit(".", 1)[-1]
        if ref in failures:
            return ExecutorResultFailure(
                error=ExecutorActionErrorInfo(
                    action_name=action_name,
                    type="ValueError",
                    message=f"rejected {CANARY}",
                    filename="probe.py",
                    function="run",
                )
            )
        return ExecutorResultSuccess(result=results.get(ref, {}).get("result", "x"))

    masks = ctx_secret_masks.get()
    assert masks is not None
    masks.observe(resolved.secrets)
    backend = mock.Mock(execute=mock.AsyncMock(side_effect=execute))
    with (
        mock.patch.object(
            service_module.registry_resolver,
            "resolve_action",
            mock.AsyncMock(side_effect=load),
        ),
        mock.patch.object(
            service_module, "_mint_action_executor_token", return_value="step-token"
        ),
    ):
        return await service_module._execute_template_action(
            backend=backend,
            input=run_input,
            ctx=service_module.DispatchActionContext(role=role),
            resolved_context=resolved,
            timeout=30,
            provenance=build_provenance(caller_args),
        )


@pytest.mark.anyio
async def test_non_secret_template_input_keeps_full_error() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"value": "abc"},
            steps=[("convert", {"n": "${{ int(inputs.value) }}"})],
            returns="ok",
            inputs={"value": "abc"},
        )

    message = str(exc_info.value)
    assert "invalid literal for int()" in message
    assert WITHHELD_TEXT not in message


@pytest.mark.anyio
async def test_secret_backed_template_input_is_masked() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"value": "${{ SECRETS.svc.value }}"},
            steps=[("convert", {"n": "${{ int(inputs.value) }}"})],
            returns="ok",
            inputs={"value": CANARY},
        )

    assert WITHHELD_TEXT not in str(exc_info.value)
    assert CANARY not in str(exc_info.value)
    assert "invalid literal for int()" in str(exc_info.value.detail)
    assert CANARY not in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_step_reference_masks_known_secret() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"value": "${{ SECRETS.svc.value }}"},
            steps=[
                ("fetch", {"token": "${{ inputs.value }}"}),
                ("convert", {"n": "${{ int(steps.fetch.result) }}"}),
            ],
            returns="ok",
            inputs={"value": CANARY},
            step_results={"fetch": {"result": CANARY}},
        )

    assert WITHHELD_TEXT not in str(exc_info.value)
    assert CANARY not in str(exc_info.value)


@pytest.mark.anyio
async def test_step_reference_without_known_secrets_keeps_full_error() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"value": "abc"},
            steps=[
                ("fetch", {"token": "${{ inputs.value }}"}),
                ("convert", {"n": "${{ int(steps.fetch.result) }}"}),
            ],
            returns="ok",
            inputs={"value": "abc"},
            step_results={"fetch": {"result": "abc"}},
        )

    message = str(exc_info.value)
    assert "invalid literal for int()" in message
    assert WITHHELD_TEXT not in message


@pytest.mark.anyio
async def test_known_secret_is_masked_across_steps() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"value": "${{ SECRETS.svc.value }}"},
            steps=[
                ("a", {"token": "${{ inputs.value }}"}),
                ("b", {"passthrough": "${{ steps.a.result }}"}),
                ("c", {"n": "${{ int(steps.b.result) }}"}),
            ],
            returns="ok",
            inputs={"value": CANARY},
            step_results={"a": {"result": CANARY}, "b": {"result": CANARY}},
        )

    assert WITHHELD_TEXT not in str(exc_info.value)
    assert CANARY not in str(exc_info.value)


@pytest.mark.anyio
async def test_step_result_masks_declared_secret_with_literal_args() -> None:
    """Secrets supplied through the environment are known invocation masks."""
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"url": "https://example.test/usage"},
            steps=[
                ("fetch", {"url": "https://example.test/usage"}),
                ("convert", {"n": "${{ int(steps.fetch.result) }}"}),
            ],
            returns="ok",
            inputs={},
            step_results={"fetch": {"result": CANARY}},
            steps_declaring_secrets={"fetch"},
        )

    message = str(exc_info.value)
    assert WITHHELD_TEXT not in message
    assert CANARY not in message


@pytest.mark.anyio
async def test_step_without_declared_secrets_keeps_full_error() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"url": "https://example.test/usage"},
            steps=[
                ("fetch", {"url": "https://example.test/usage"}),
                ("convert", {"n": "${{ int(steps.fetch.result) }}"}),
            ],
            returns="ok",
            inputs={},
            step_results={"fetch": {"result": "abc"}},
        )

    message = str(exc_info.value)
    assert "invalid literal for int()" in message
    assert WITHHELD_TEXT not in message


@pytest.mark.anyio
async def test_returns_expression_follows_the_same_rules() -> None:
    with pytest.raises(TracecatExpressionError) as exc_info:
        await _run_template(
            caller_args={"value": "${{ SECRETS.svc.value }}"},
            steps=[("fetch", {"token": "${{ inputs.value }}"})],
            returns="${{ int(steps.fetch.result) }}",
            inputs={"value": CANARY},
            step_results={"fetch": {"result": CANARY}},
        )

    assert WITHHELD_TEXT not in str(exc_info.value)

    with pytest.raises(TracecatExpressionError) as safe_info:
        await _run_template(
            caller_args={"value": "abc"},
            steps=[("fetch", {"token": "${{ inputs.value }}"})],
            returns="${{ int(steps.fetch.result) }}",
            inputs={"value": "abc"},
            step_results={"fetch": {"result": "abc"}},
        )

    assert "invalid literal for int()" in str(safe_info.value)


@pytest.mark.parametrize(
    ("caller_args", "inputs", "declaring", "masked"),
    [
        pytest.param({"value": "abc"}, {"value": "abc"}, set(), False, id="clean"),
        pytest.param(
            {"value": "${{ SECRETS.svc.value }}"},
            {"value": CANARY},
            set(),
            True,
            id="secret-input",
        ),
        pytest.param(
            {"value": "abc"},
            {"value": "abc"},
            {"probe"},
            True,
            id="declared-secret",
        ),
    ],
)
@pytest.mark.anyio
async def test_template_action_error_masks_known_values(
    caller_args: dict[str, Any],
    inputs: dict[str, Any],
    declaring: set[str],
    masked: bool,
) -> None:
    from tracecat.exceptions import ExecutionError

    with pytest.raises(ExecutionError) as exc_info:
        await _run_template(
            caller_args=caller_args,
            steps=[("probe", {"value": "${{ inputs.value }}"})],
            returns="ok",
            inputs=inputs,
            steps_declaring_secrets=declaring,
            failing_steps={"probe"},
        )

    message = str(exc_info.value)
    if masked:
        assert "Details withheld:" not in message
        assert "rejected" in message
        assert CANARY not in message
    else:
        assert CANARY in message


@pytest.mark.parametrize(
    "carrier",
    [
        "${{ ACTIONS.fetch.result }}",
        "${{ var.item }}",
        "${{ steps.fetch.result }}",
    ],
)
def test_known_runtime_carrier_renamed_to_child_input_is_masked(carrier: str) -> None:
    child = build_provenance({"value": carrier})

    masks = ctx_secret_masks.get()
    assert masks is not None
    masks.observe(CANARY)

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"n": "${{ int(inputs.value) }}"},
            operand={ExprContext.TEMPLATE_ACTION_INPUTS: {"value": CANARY}},
            provenance=child,
        )

    assert WITHHELD_TEXT not in str(exc_info.value)
    assert CANARY not in str(exc_info.value)
    assert CANARY not in repr(exc_info.value.detail)
    for link in _walk_exception_chain(exc_info.value):
        assert CANARY not in str(link)


def test_non_carrier_renamed_to_child_input_keeps_full_error() -> None:
    child = build_provenance({"value": "${{ TRIGGER.value }}"})

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"n": "${{ int(inputs.value) }}"},
            operand={ExprContext.TEMPLATE_ACTION_INPUTS: {"value": "abc"}},
            provenance=child,
        )

    message = str(exc_info.value)
    assert "invalid literal for int()" in message
    assert WITHHELD_TEXT not in message


def test_known_carrier_is_masked_through_nested_inputs() -> None:
    parent = build_provenance({"v": "${{ ACTIONS.fetch.result }}"})
    child = build_provenance(
        {"value": "${{ inputs.v }}"},
        parent,
    )

    masks = ctx_secret_masks.get()
    assert masks is not None
    masks.observe(CANARY)

    with pytest.raises(TracecatExpressionError) as exc_info:
        eval_templated_object(
            {"n": "${{ int(inputs.value) }}"},
            operand={ExprContext.TEMPLATE_ACTION_INPUTS: {"value": CANARY}},
            provenance=child,
        )

    assert WITHHELD_TEXT not in str(exc_info.value)
    assert CANARY not in str(exc_info.value)


def test_loop_context_withholds_loop_variables() -> None:
    """Loop diagnostics identify the iteration without copying the input value."""

    from tracecat.executor.service import _attach_loop_context

    with pytest.raises(ValueError) as exc_info:
        int(CANARY)
    info = ExecutorActionErrorInfo.from_exc(exc_info.value, action_name="a")
    info.message = "bad loop item"

    _attach_loop_context(info, 0)

    assert info.loop_vars is None
    rendered = str(info)
    assert CANARY not in rendered
    assert "Iteration 0" in rendered
