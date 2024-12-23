from tracecat.expressions.functions import eval_jsonpath
from tracecat.parse import traverse_expressions, traverse_leaves


def test_iter_dict_leaves():
    # Test case 1: Nested dictionary
    obj1 = d = {
        "a": {"b": {"c": 1}, "d": 2},
        "e": 3,
        "f": [{"g": 4}, {"h": 5}],
        "i": [6, 7],
    }
    expected1 = [
        ("a.b.c", 1),
        ("a.d", 2),
        ("e", 3),
        ("f[0].g", 4),
        ("f[1].h", 5),
        ("i[0]", 6),
        ("i[1]", 7),
    ]
    actual = list(traverse_leaves(obj1))
    assert actual == expected1

    # Test that the jsonpath expressions are valid
    for path, expected_value in actual:
        actual_value = eval_jsonpath(path, d)
        assert actual_value == expected_value


def test_more_iter_dict_leaves():
    # Test case 2: Empty dictionary
    obj2 = {}
    expected2 = []
    assert list(traverse_leaves(obj2)) == expected2

    # Test case 3: Dictionary with empty values
    obj3 = {"a": {}, "b": {"c": {}}, "d": []}
    expected3 = []
    assert list(traverse_leaves(obj3)) == expected3

    # Test case 4: Dictionary with non-dict values
    obj4 = {"a": 1, "b": [2, 3], "c": "hello"}
    expected4 = [("a", 1), ("b[0]", 2), ("b[1]", 3), ("c", "hello")]
    assert list(traverse_leaves(obj4)) == expected4


def test_traverse_expressions():
    # Test case 1: Single expression in string
    data = {
        "test": "Hello, ${{ var.name }}",
    }
    assert list(traverse_expressions(data)) == ["var.name"]

    # Test case 2: Multiple expressions in string
    data = {
        "test": "This is a ${{ 1 }} or ${{ 2 }}",
    }
    assert list(traverse_expressions(data)) == ["1", "2"]

    # Test case 3: Nested expressions in objects and lists
    data = {
        "test": "This is a ${{ 1 }} or ${{ 2 }}",
        "list": [
            "This is a ${{ 3 }} or ${{ 4 }}",
            "second",
            {
                "test": "This is a ${{ 5 }} or ${{ 6 }}",
            },
        ],
        "data": "${{ 7 }}${{ 8 }}",
    }
    assert list(traverse_expressions(data)) == ["1", "2", "3", "4", "5", "6", "7", "8"]

    # Test case 4: No expressions
    data = {
        "test": "Hello world",
        "list": ["no expressions", {"test": 123}],
    }
    assert list(traverse_expressions(data)) == []

    # Test case 5: Empty data structures
    data = {}
    assert list(traverse_expressions(data)) == []
    data = {"test": {}, "list": []}
    assert list(traverse_expressions(data)) == []
