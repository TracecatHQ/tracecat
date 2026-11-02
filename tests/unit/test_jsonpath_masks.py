"""Native JSONPath transformations retain observed secret dependencies."""

from collections.abc import Iterator

import pytest

from tracecat.contexts import ctx_secret_masks
from tracecat.exceptions import TracecatExpressionError
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.executor.service import _sanitize_error_info
from tracecat.expressions.common import eval_jsonpath, parse_jsonpath
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.jsonpath import find_with_secret_masks
from tracecat.secrets.masking import SecretMaskCollector

SECRET = "syntheticcredential-private-value"


@pytest.fixture
def masks() -> Iterator[SecretMaskCollector]:
    collector = SecretMaskCollector()
    token = ctx_secret_masks.set(collector)
    try:
        yield collector
    finally:
        ctx_secret_masks.reset(token)


@pytest.mark.parametrize(
    "context", ["steps", "ACTIONS", "var", "TRIGGER", "ENV", "inputs"]
)
@pytest.mark.parametrize(
    ("transform", "fragment"),
    [
        ("`split(-, 0, -1)`", "syntheticcredential"),
        ("`sub(/-private-value/, )`", "syntheticcredential"),
        ("`split(-, 0, -1)`.`sub(/credential/, )`", "synthetic"),
    ],
)
def test_jsonpath_fragments_are_masked_through_error_transport(
    masks: SecretMaskCollector, context: str, transform: str, fragment: str
) -> None:
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(" + context + ".value." + transform + ") }}",
            operand={
                "SECRETS": {"api": {"TOKEN": SECRET}},
                context: {"value": SECRET},
            },
        )
    assert fragment in masks.values
    assert "ValueError in int(***)" in str(caught.value)
    assert fragment not in str(caught.value)
    assert fragment not in str(caught.value.detail)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    info = ExecutorActionErrorInfo.from_exc(caught.value, action_name="testing.probe")
    assert fragment not in _sanitize_error_info(info, None).message


@pytest.mark.parametrize(
    "path",
    [
        "TRIGGER.payload.public.`split(-, 0, -1)`",
        "(TRIGGER.values[*].`split(-, 0, -1)`)[1]",
        "(TRIGGER..value.`split(-, 0, -1)`)[1]",
        "(TRIGGER.payload.`keys`.`split(-, 0, -1)`)[1]",
        "(TRIGGER.keys.`keys`.`split(-, 0, -1)`)[1]",
        "(TRIGGER.keys.`sorted`)[0]",
    ],
)
def test_public_jsonpath_results_keep_diagnostics(
    masks: SecretMaskCollector, path: str
) -> None:
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ int(" + path + ") }}",
            operand={
                "SECRETS": {"api": {"TOKEN": SECRET}},
                "TRIGGER": {
                    "payload": {"token": SECRET, "public": "publicvalue-suffix"},
                    "values": [SECRET, "publicvalue-suffix"],
                    "nested": [{"value": SECRET}, {"value": "publicvalue-suffix"}],
                    "keys": {SECRET: "public-data", "public": "public-data"},
                },
            },
        )
    expected = "public" if "keys" in path else "publicvalue"
    assert expected in str(caught.value.detail)
    assert expected not in masks.values
    assert "invalid literal for int()" in str(caught.value)


def test_jsonpath_transform_failure_has_no_secret_operand_or_chain(
    masks: SecretMaskCollector,
) -> None:
    # JSONPath string substitution expects a string. Protect a sensitive
    # operand even when its transformation fails before returning a value.
    with pytest.raises(TracecatExpressionError) as caught:
        eval_templated_object(
            "${{ TRIGGER.value.`sub(/private/, public)` }}",
            operand={
                "SECRETS": {"api": {"TOKEN": SECRET}},
                "TRIGGER": {"value": [SECRET]},
            },
        )
    assert "TypeError in JSONPath Sub(***)" in str(caught.value)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    ("path", "data", "sensitive", "expected"),
    [
        ("$.value.`len`", {"value": SECRET}, SECRET, len(SECRET)),
        ("$.value.`str()`", {"value": 54321}, "54321", "54321"),
        ("$.value.`sorted`", {"value": [SECRET, "public"]}, SECRET, ["public", SECRET]),
        ("$.value.`keys`", {"value": {SECRET: "public"}}, SECRET, SECRET),
        ("$.value.*.`path`", {"value": {SECRET: "public"}}, SECRET, SECRET),
        ("$.value + 1", {"value": 54321}, "54321", 54322),
        ("$.public + 1", {"value": 54321, "public": 10}, "54321", 11),
        ("$.value + 1", {"value": SECRET}, SECRET, None),
    ],
)
def test_observation_preserves_jsonpath_evaluation(
    path, data, sensitive, expected
) -> None:
    masks = SecretMaskCollector()
    masks.observe(sensitive)
    original = eval_jsonpath(path, data)
    observed = eval_jsonpath(
        path,
        data,
        find=lambda node, operand: find_with_secret_masks(node, operand, masks),
    )
    assert observed == original == expected
    if path == "$.value + 1" and isinstance(expected, int):
        assert str(expected) in masks.values
    if path == "$.public + 1":
        assert str(expected) not in masks.values


def test_cached_jsonpath_does_not_share_observers_or_re_evaluate_inputs() -> None:
    class CountingDict(dict[str, object]):
        reads = 0

        def get(self, key: str, default: object = None) -> object:
            self.reads += 1
            return super().get(key, default)

    path = parse_jsonpath("$.value.`split(-, 0, -1)`")
    original_tree = repr(path)
    first, second = SecretMaskCollector(), SecretMaskCollector()
    first.observe(SECRET)
    second.observe("unrelated-credential")
    first_data, second_data = CountingDict(value=SECRET), CountingDict(value=SECRET)
    assert (
        find_with_secret_masks(path, first_data, first)[0].value
        == "syntheticcredential"
    )
    assert (
        find_with_secret_masks(path, second_data, second)[0].value
        == "syntheticcredential"
    )
    assert "syntheticcredential" in first.values
    assert "syntheticcredential" not in second.values
    assert repr(path) == original_tree
    assert first_data.reads == second_data.reads == 1


@pytest.mark.parametrize(
    "path",
    [
        '$.items[?(@.token.`split(-, 0, -1)` == "syntheticcredential")].token',
        "$.items[/token.`split(-, 0, -1)`]",
    ],
)
def test_transforms_inside_filters_and_sort_keys_are_observed(path: str) -> None:
    masks = SecretMaskCollector()
    masks.observe(SECRET)
    data = {"items": [{"token": SECRET}, {"token": "publicvalue-suffix"}]}
    expected = eval_jsonpath(path, data)
    result = eval_jsonpath(
        path,
        data,
        find=lambda node, operand: find_with_secret_masks(node, operand, masks),
    )
    assert result == expected
    assert "syntheticcredential" in masks.values
    assert "publicvalue" not in masks.values
