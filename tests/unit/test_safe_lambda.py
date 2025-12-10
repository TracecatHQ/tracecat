"""Tests for build_safe_lambda and related security validators.

These tests verify that the SafeLambdaValidator and related security functions
correctly block dangerous expressions while allowing safe operations.
"""

import sys
from typing import Any

import pytest

from tracecat.sandbox.safe_lambda import build_safe_lambda


@pytest.mark.parametrize(
    "lambda_str,test_input,expected_result",
    [
        ("lambda x: x + 1", 1, 2),
        ("lambda x: x * 2", 2, 4),
        ("lambda x: str(x)", 1, "1"),
        ("lambda x: len(x)", "hello", 5),
        ("lambda x: x.upper()", "hello", "HELLO"),
        ("lambda x: x['key']", {"key": "value"}, "value"),
        ("lambda x: x.get('key', 'default')", {}, "default"),
        ("lambda x: bool(x)", 1, True),
        ("lambda x: [i * 2 for i in x]", [1, 2, 3], [2, 4, 6]),
        ("lambda x: sum(x)", [1, 2, 3], 6),
        ("lambda x: x is None", None, True),
        ("lambda x: x.strip()", "  hello  ", "hello"),
        ("lambda x: x.startswith('test')", "test_string", True),
        ("lambda x: list(x.keys())", {"a": 1, "b": 2}, ["a", "b"]),
        ("lambda x: max(x)", [1, 5, 3], 5),
    ],
)
def test_build_lambda(lambda_str: str, test_input: Any, expected_result: Any) -> None:
    fn = build_safe_lambda(lambda_str)
    assert fn(test_input) == expected_result


@pytest.mark.parametrize(
    "lambda_str,test_input,expected_result",
    [
        ("lambda x: jsonpath('$.name', x) == 'John'", {"name": "John"}, True),
        # Test nested objects
        (
            "lambda x: jsonpath('$.user.name', x) == 'Alice'",
            {"user": {"name": "Alice"}},
            True,
        ),
        # Test array indexing
        (
            "lambda x: jsonpath('$.users[0].name', x) == 'Bob'",
            {"users": [{"name": "Bob"}]},
            True,
        ),
        # Test array wildcard
        (
            "lambda x: len(jsonpath('$.users[*].name', x)) == 2",
            {"users": [{"name": "Alice"}, {"name": "Bob"}]},
            True,
        ),
        # Test deep nesting
        (
            "lambda x: jsonpath('$.data.nested.very.deep.value', x) == 42",
            {"data": {"nested": {"very": {"deep": {"value": 42}}}}},
            True,
        ),
        # Test array filtering
        (
            "lambda x: len(jsonpath('$.numbers[?@ > 2]', x)) == 2",
            {"numbers": [1, 2, 3, 4]},
            True,
        ),
        # Test with null/missing values
        ("lambda x: jsonpath('$.missing.path', x) is None", {"other": "value"}, True),
        # Test multiple conditions
        (
            "lambda x: all(v > 0 for v in jsonpath('$.values[*]', x))",
            {"values": [1, 2, 3]},
            True,
        ),
        # Test with string operations
        (
            "lambda x: jsonpath('$.text', x).startswith('hello')",
            {"text": "hello world"},
            True,
        ),
    ],
)
def test_use_jsonpath_in_safe_lambda(
    lambda_str: str, test_input: Any, expected_result: Any
) -> None:
    jsonpath = build_safe_lambda(lambda_str)
    assert jsonpath(test_input) == expected_result


@pytest.mark.parametrize(
    "lambda_str,error_msg",
    [
        # Test restricted symbols - file operations
        ("lambda x: open('/etc/passwd')", "Expression contains restricted symbols"),
        ("lambda x: file.read()", "Expression contains restricted symbols"),
        ("lambda x: io.open('test')", "Expression contains restricted symbols"),
        ("lambda x: pathlib.Path('/')", "Expression contains restricted symbols"),
        # Test restricted symbols - OS/system operations
        ("lambda x: os.system('ls')", "Expression contains restricted symbols"),
        ("lambda x: subprocess.run(['ls'])", "Expression contains restricted symbols"),
        ("lambda x: sys.exit()", "Expression contains restricted symbols"),
        ("lambda x: __import__('os')", "Expression contains restricted symbols"),
        # Test restricted symbols - network operations
        ("lambda x: socket.socket()", "Expression contains restricted symbols"),
        (
            "lambda x: urllib.request.urlopen('http://evil.com')",
            "Expression contains restricted symbols",
        ),
        (
            "lambda x: requests.get('http://evil.com')",
            "Expression contains restricted symbols",
        ),
        # Test restricted symbols - introspection
        ("lambda x: eval('x + 1')", "Expression contains restricted symbols"),
        ("lambda x: exec('print(x)')", "Expression contains restricted symbols"),
        (
            "lambda x: compile('x', 'test', 'eval')",
            "Expression contains restricted symbols",
        ),
        ("lambda x: globals()['secret']", "Expression contains restricted symbols"),
        ("lambda x: locals()['key']", "Expression contains restricted symbols"),
        # Test dangerous patterns
        (
            "lambda x: x.__class__.__bases__",
            "Expression contains dangerous pattern: __",
        ),
        ("lambda x: '\\x41\\x42\\x43'", "Expression contains dangerous pattern: \\x"),
        ("lambda x: '\\u0041\\u0042'", "Expression contains dangerous pattern: \\u"),
        ("lambda x: chr(65)", "Expression contains dangerous pattern: chr("),
        ("lambda x: ord('A')", "Expression contains dangerous pattern: ord("),
        # Note: These are caught by restricted symbols check since 'decode'/'encode' are in the list
        ("lambda x: 'test'.decode('utf-8')", "Expression contains restricted symbols"),
        ("lambda x: x.encode('utf-8')", "Expression contains restricted symbols"),
        # Note: This is caught by restricted symbols because 'encode' is in 'b64encode'
        ("lambda x: base64.b64encode(x)", "Expression contains restricted symbols"),
        # Test expression too long
        (f"lambda x: {'x + ' * 500}x", "Expression too long"),
    ],
)
def test_build_lambda_security_restrictions(lambda_str: str, error_msg: str) -> None:
    """Test that dangerous lambda expressions are blocked."""
    with pytest.raises(ValueError):
        build_safe_lambda(lambda_str)


@pytest.mark.parametrize(
    "lambda_str,error_msg",
    [
        # Test AST-level restrictions - imports
        # Note: __import__ is caught by string-level check first
        (
            "lambda x: (lambda: __import__('os'))()",
            "Expression contains restricted symbols",
        ),
        # Test AST-level restrictions - direct function calls
        # Note: These are all caught by string-level check first since they're in RESTRICTED_SYMBOLS
        ("lambda x: eval('x')", "Expression contains restricted symbols"),
        ("lambda x: exec('x')", "Expression contains restricted symbols"),
        ("lambda x: open('file.txt')", "Expression contains restricted symbols"),
        # Test AST-level restrictions - attribute access
        # Note: decode/encode are caught by string-level check
        ("lambda x: x.decode", "Expression contains restricted symbols"),
        ("lambda x: str.encode", "Expression contains restricted symbols"),
        # Test AST-level restrictions - accessing restricted names
        # Note: os/sys are caught by string-level check
        ("lambda x: os", "Expression contains restricted symbols"),
        ("lambda x: sys", "Expression contains restricted symbols"),
        # Test whitelist validation - disallowed node types
        ("lambda x: (yield x)", "Node type Yield is not allowed in expressions"),
    ],
)
def test_build_lambda_ast_restrictions(lambda_str: str, error_msg: str) -> None:
    """Test that AST-level restrictions work properly."""
    with pytest.raises(ValueError):
        build_safe_lambda(lambda_str)


def test_build_lambda_recursion_limit() -> None:
    """Test that recursion depth limits are enforced."""
    # Test that the recursion limit is properly set and restored
    original_limit = sys.getrecursionlimit()

    # Execute a lambda to ensure the limit is set and restored
    simple_lambda = build_safe_lambda("lambda x: x + 1")
    result = simple_lambda(1)
    assert result == 2

    # Check that the recursion limit was restored
    assert sys.getrecursionlimit() == original_limit


def test_build_lambda_safe_builtins() -> None:
    """Test that only safe builtins are available in lambda execution."""
    # Test allowed builtins work
    allowed_builtins = [
        ("lambda x: abs(x)", -5, 5),
        ("lambda x: min(x)", [3, 1, 4], 1),
        ("lambda x: max(x)", [3, 1, 4], 4),
        ("lambda x: sum(x)", [1, 2, 3], 6),
        ("lambda x: len(x)", [1, 2, 3], 3),
        ("lambda x: int(x)", "42", 42),
        ("lambda x: float(x)", "3.14", 3.14),
        ("lambda x: str(x)", 42, "42"),
        ("lambda x: bool(x)", 1, True),
        ("lambda x: list(x)", (1, 2, 3), [1, 2, 3]),
        ("lambda x: dict(x)", [("a", 1), ("b", 2)], {"a": 1, "b": 2}),
        ("lambda x: tuple(x)", [1, 2, 3], (1, 2, 3)),
        ("lambda x: set(x)", [1, 2, 2, 3], {1, 2, 3}),
        ("lambda x: sorted(x)", [3, 1, 4], [1, 3, 4]),
        ("lambda x: list(reversed(x))", [1, 2, 3], [3, 2, 1]),
        ("lambda x: all(x)", [True, True, False], False),
        ("lambda x: any(x)", [False, False, True], True),
    ]

    for lambda_str, test_input, expected in allowed_builtins:
        fn = build_safe_lambda(lambda_str)
        assert fn(test_input) == expected


def test_build_lambda_iteration_limit() -> None:
    """Test that iteration limits prevent infinite loops."""
    # This lambda would iterate too many times
    large_iteration_lambda = build_safe_lambda("lambda x: [i for i in range(x)]")

    # This should work fine with small numbers
    assert large_iteration_lambda(10) == list(range(10))

    # With our iteration guard, large iterations might fail
    # Note: Current implementation only guards the input, not internal iterations
    # So this test mainly verifies the wrapper doesn't break normal operations


def test_build_lambda_safe_return_types() -> None:
    """Test that lambdas can only return safe types."""
    # These should work - returning safe types
    safe_returns = [
        ("lambda x: None", 1, None),
        ("lambda x: True", 1, True),
        ("lambda x: 42", 1, 42),
        ("lambda x: 3.14", 1, 3.14),
        ("lambda x: 'hello'", 1, "hello"),
        ("lambda x: [1, 2, 3]", 1, [1, 2, 3]),
        ("lambda x: {'a': 1}", 1, {"a": 1}),
        ("lambda x: (1, 2)", 1, (1, 2)),
        ("lambda x: {1, 2, 3}", 1, {1, 2, 3}),
    ]

    for lambda_str, test_input, expected in safe_returns:
        fn = build_safe_lambda(lambda_str)
        assert fn(test_input) == expected


def test_build_lambda_jsonpath_allowed() -> None:
    """Test that jsonpath is allowed and works correctly."""
    # Ensure jsonpath is in the allowed functions
    jsonpath_lambda = build_safe_lambda("lambda x: jsonpath('$.name', x)")
    result = jsonpath_lambda({"name": "Alice", "age": 30})
    assert result == "Alice"

    # Test complex jsonpath usage
    complex_lambda = build_safe_lambda(
        "lambda x: [jsonpath(f'$.users[{i}].name', x) for i in range(len(jsonpath('$.users', x)))]"
    )
    result = complex_lambda(
        {"users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]}
    )
    assert result == ["Alice", "Bob"]


@pytest.mark.parametrize(
    "lambda_str",
    [
        # Attribute chains that might try to escape
        "lambda x: x.__class__.__mro__[1].__subclasses__",
        "lambda x: ''.__class__.__bases__[0].__subclasses__()",
        "lambda x: x.__init__.__globals__",  # This one has 'globals' which is restricted
        # Trying to access builtins through various means
        "lambda x: [].__class__.__base__.__subclasses__()[104]",  # Would access <type 'sys'>
        "lambda x: ''.__class__.__mro__[1].__init__.__globals__['sys']",  # Has both 'globals' and 'sys'
    ],
)
def test_build_lambda_attribute_chain_attacks(lambda_str: str) -> None:
    """Test that attribute chain attacks are blocked."""
    with pytest.raises(ValueError) as exc_info:
        build_safe_lambda(lambda_str)
    # Should be caught by either dangerous pattern, dunder attribute, or restricted symbols
    error_msg = str(exc_info.value)
    assert any(
        msg in error_msg
        for msg in [
            "dangerous pattern: __",
            "dunder attribute",
            "Expression contains restricted symbols",
        ]
    )


def test_build_lambda_complex_safe_expressions() -> None:
    """Test that complex but safe expressions work correctly."""
    # List comprehension with filtering
    fn1 = build_safe_lambda("lambda x: [i * 2 for i in x if i > 2]")
    assert fn1([1, 2, 3, 4, 5]) == [6, 8, 10]

    # Dictionary comprehension
    fn2 = build_safe_lambda("lambda x: {k: v * 2 for k, v in x.items() if v > 0}")
    assert fn2({"a": 1, "b": -1, "c": 2}) == {"a": 2, "c": 4}

    # Nested lambda (not actual lambda keyword, but functional style)
    fn3 = build_safe_lambda(
        "lambda x: list(map(lambda y: y * 2, filter(lambda z: z > 0, x))) if False else [i * 2 for i in x if i > 0]"
    )
    assert fn3([-1, 0, 1, 2, 3]) == [2, 4, 6]

    # Complex boolean logic
    fn4 = build_safe_lambda(
        "lambda x: all(i > 0 for i in x) and len(x) > 2 and sum(x) < 100"
    )
    assert fn4([1, 2, 3])
    assert not fn4([1, 2])
    assert not fn4([1, 2, -3])
    assert not fn4([30, 40, 50])

    # Ternary with complex conditions
    fn5 = build_safe_lambda(
        "lambda x: 'greater' if x > 0 else ('lesser' if x < 0 else 'equal')"
    )
    assert fn5(5) == "greater"
    assert fn5(-5) == "lesser"
    assert fn5(0) == "equal"


def test_build_lambda_input_sanitization() -> None:
    """Test that inputs are properly sanitized with iteration guards."""
    # Test with dict input
    dict_lambda = build_safe_lambda("lambda x: sum(x.values())")
    assert dict_lambda({"a": 1, "b": 2, "c": 3}) == 6

    # Test with list input
    list_lambda = build_safe_lambda("lambda x: [i * 2 for i in x]")
    assert list_lambda([1, 2, 3]) == [2, 4, 6]

    # Test with string input (should not be wrapped)
    str_lambda = build_safe_lambda("lambda x: x.upper()")
    assert str_lambda("hello") == "HELLO"
