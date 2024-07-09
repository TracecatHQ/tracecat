import pytest

from tracecat.expressions.functions import lambda_filter


def test_lambda_filter_success():
    items = [1, 2, 3, 4, 5, 6, 7]

    expr = "x > 2"
    assert lambda_filter(items, expr) == [3, 4, 5, 6, 7]

    expr = "x > 2 and x < 6"
    assert lambda_filter(items, expr) == [3, 4, 5]

    expr = "x in [1,2,3]"
    assert lambda_filter(items, expr) == [1, 2, 3]

    listoflists = [[1, 2, 3], [4, 5, 6, 7, 8, 9], [10, 11], []]

    expr = "2 in x or 11 in x"
    assert lambda_filter(listoflists, expr) == [[1, 2, 3], [10, 11]]

    expr = "len(x) > 0"
    assert lambda_filter(listoflists, expr) == [[1, 2, 3], [4, 5, 6, 7, 8, 9], [10, 11]]

    expr = "len(x) < 3"
    assert lambda_filter(listoflists, expr) == [[10, 11], []]

    strlist = ["test@tracecat.com", "user@tracecat.com", "user@other.com"]
    expr = "'tracecat.com' in x"
    assert lambda_filter(strlist, expr) == ["test@tracecat.com", "user@tracecat.com"]


def test_lambda_filter_fails():
    items = [1, 2, 3, 4, 5, 6, 7]
    with pytest.raises(ValueError):
        lambda_filter(items, "sum(x)")

    listoflists = [[1, 2, 3], [4, 5, 6, 7, 8, 9], [10, 11], []]
    with pytest.raises(ValueError):
        lambda_filter(listoflists, "sum(x) > 1")

    with pytest.raises(ValueError):
        lambda_filter(items, "x + 1")
    with pytest.raises(ValueError):
        lambda_filter(items, "y + 1")
    with pytest.raises(ValueError):
        lambda_filter(items, "y > 1")

    with pytest.raises(ValueError):
        lambda_filter(items, "x > 2 and x < 6 and x + 1")

    with pytest.raises(ValueError):
        lambda_filter(items, "lambda x: x > 2")

    with pytest.raises(ValueError):
        lambda_filter(items, "eval()")

    with pytest.raises(ValueError):
        lambda_filter(items, "import os")
