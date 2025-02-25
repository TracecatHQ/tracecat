# TODO: Add require to workflow tests
# NOTE: We instead test `eval_templated_object` directly to check if binary ops (e.g. ==, !=, >, <, etc.) all work: `test_expression_binary_ops` in `unit/test_expressions.py`
# AKA we assume-by-induction that `core.require` works as expected, since `core.require` functions like `core.transform.reshape`.

import pytest
from tracecat_registry.base.core.require import require


@pytest.mark.parametrize(
    "conditions",
    [
        True,
        [True, True],
        [True, True, True],
    ],
)
def test_require_all(conditions):
    assert require(conditions) is True


@pytest.mark.parametrize(
    "conditions",
    [
        False,
        [True, False],
        [False, True],
    ],
)
def test_require_all_fail(conditions):
    with pytest.raises(AssertionError):
        require(conditions)
