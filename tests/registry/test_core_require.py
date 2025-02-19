# TODO: Add require to workflow tests
# NOTE: We instead test `eval_templated_object` directly to check if binary ops (e.g. ==, !=, >, <, etc.) all work: `test_expression_binary_ops` in `unit/test_expressions.py`
# AKA we assume-by-induction that `core.require` works as expected, since `core.require` functions like `core.transform.reshape`.

import pytest
from tracecat_registry.base.core.require import require


@pytest.mark.parametrize(
    "exprs",
    [
        True,
        [True, True],
        [True, True, True],
    ],
)
def test_require_all(exprs):
    assert require(exprs) is True


@pytest.mark.parametrize(
    "exprs",
    [
        False,
        [True, False],
        [False, True],
    ],
)
def test_require_all_fail(exprs):
    with pytest.raises(AssertionError):
        require(exprs)
