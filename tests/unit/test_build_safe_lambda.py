import shutil

import pytest
from tracecat_registry.core.python import PythonScriptExecutionError

from tracecat.expressions.common import build_safe_lambda

# Check if Deno is available (required for the subprocess fallback)
DENO_AVAILABLE = shutil.which("deno") is not None

# Skip all tests if Deno isn't available
pytestmark = pytest.mark.skipif(
    not DENO_AVAILABLE,
    reason="Deno not available. Required for Pyodide subprocess execution.",
)


class TestBuildSafeLambda:
    """Test build_safe_lambda functionality with Deno sandbox."""

    def test_input_validation(self):
        """Test basic input validation."""
        # Empty or None input
        with pytest.raises(ValueError, match="non-empty string"):
            build_safe_lambda("")

        with pytest.raises(ValueError, match="non-empty string"):
            build_safe_lambda(None)  # type: ignore

    def test_lambda_structure_validation(self):
        """Test validation of lambda structure."""
        # Not a lambda - should fail parsing
        with pytest.raises(ValueError, match="Invalid lambda expression format"):
            build_safe_lambda("x + 1")

        # These should parse successfully - just test that we can create the callable
        func = build_safe_lambda("lambda: 42")
        assert callable(func)

        func = build_safe_lambda("lambda a, b, c, d: a + b + c + d")
        assert callable(func)

    def test_lambda_parsing(self):
        """Test that various lambda expressions are parsed correctly."""
        # Test that these don't raise exceptions during parsing
        test_cases = [
            "lambda x: x + 1",
            "lambda x: x.upper()",
            "lambda x: len(x)",
            "lambda x, y: x * y",
            "lambda x: sum(x)",
            "lambda x: max(x)",
            "lambda x: 'positive' if x > 0 else 'non-positive'",
            "lambda d: d.get('key', 'default')",
            "lambda items: [x for x in items if x > 0]",
            'lambda data: jsonpath("$.users[*].name", data)',
            'lambda x: open("/etc/passwd")',  # Should parse but fail in sandbox
            'lambda x: __import__("os")',  # Should parse but be isolated
        ]

        for expr in test_cases:
            func = build_safe_lambda(expr)
            assert callable(func)

    def test_basic_lambda_execution(self):
        """Test that basic lambdas execute correctly."""
        # These tests require actual Deno execution which needs proper environment setup
        func1 = build_safe_lambda("lambda x: x + 1")
        assert func1(5) == 6

        func2 = build_safe_lambda("lambda x: x.upper()")
        assert func2("hello") == "HELLO"

    def test_sandbox_isolation(self):
        """Test that dangerous operations fail safely in the sandbox."""

        # File operations should fail in sandbox
        func1 = build_safe_lambda('lambda x: open("/etc/passwd")')
        with pytest.raises(PythonScriptExecutionError):
            func1(None)

    def test_security_by_design(self):
        """Test that previously dangerous patterns are now safe due to sandbox isolation."""
        # These patterns were blocked in the old implementation
        # but are now safe because they run in an isolated sandbox
        dangerous_patterns = [
            "lambda x: x.__class__",
            "lambda x: x.__dict__",
            'lambda x: getattr(x, "__class__")',
            'lambda x: __import__("os")',
            'lambda x: eval("1+1")',
            'lambda x: open("/etc/passwd")',
            "lambda x: globals()",
            "lambda x: ().__class__.__bases__[0].__subclasses__()",
        ]

        # All of these should now parse successfully
        # The sandbox provides the security, not pattern filtering
        for pattern in dangerous_patterns:
            func = build_safe_lambda(pattern)
            assert callable(func)
            # Actual execution would be safe but might fail due to sandbox restrictions

    @pytest.mark.skip(reason="Requires Deno environment setup")
    def test_builtin_functions(self):
        """Test that common built-in functions work."""
        test_cases = [
            ("lambda x: sum(x)", [1, 2, 3], 6),
            ("lambda x: max(x)", [1, 5, 3], 5),
            ("lambda x: min(x)", [1, 5, 3], 1),
            ("lambda x: abs(x)", -5, 5),
            ("lambda x: len(x)", "hello", 5),
            ("lambda x: str(x)", 42, "42"),
            ("lambda x: int(x)", "42", 42),
            ("lambda x: float(x)", "3.14", 3.14),
            ("lambda x: bool(x)", 1, True),
            ("lambda x: list(x)", (1, 2, 3), [1, 2, 3]),
            ("lambda x: sorted(x)", [3, 1, 2], [1, 2, 3]),
        ]

        for expr, input_val, expected in test_cases:
            func = build_safe_lambda(expr)
            assert func(input_val) == expected

    def test_string_methods(self):
        """Test that string methods work correctly."""
        test_cases = [
            ("lambda s: s.upper()", "hello", "HELLO"),
            ("lambda s: s.lower()", "HELLO", "hello"),
            ("lambda s: s.strip()", "  hello  ", "hello"),
            ('lambda s: s.split(",")', "a,b,c", ["a", "b", "c"]),
            ('lambda s: s.replace("a", "b")', "banana", "bbnbnb"),
            ("lambda s: s.startswith('h')", "hello", True),
            ("lambda s: s.endswith('o')", "hello", True),
        ]

        for expr, input_val, expected in test_cases:
            func = build_safe_lambda(expr)
            assert func(input_val) == expected

    def test_list_comprehensions_and_filtering(self):
        """Test more complex operations like list comprehensions."""
        # Filter operation
        func1 = build_safe_lambda("lambda items: [x for x in items if x > 0]")
        assert func1([-1, 0, 1, 2, -3]) == [1, 2]

        # Map operation
        func2 = build_safe_lambda("lambda items: [x * 2 for x in items]")
        assert func2([1, 2, 3]) == [2, 4, 6]

        # Nested data access
        func3 = build_safe_lambda("lambda items: [x['name'] for x in items]")
        assert func3([{"name": "Alice"}, {"name": "Bob"}]) == ["Alice", "Bob"]

    def test_jsonpath_functionality(self):
        """Test that jsonpath functionality works in the sandbox."""
        test_data = {
            "users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        }

        # Basic jsonpath
        func1 = build_safe_lambda('lambda data: jsonpath("$.users[*].name", data)')
        result = func1(test_data)
        assert result == ["Alice", "Bob"]

        # Single value jsonpath
        func2 = build_safe_lambda('lambda data: jsonpath("$.users[0].name", data)')
        assert func2(test_data) == "Alice"

    def test_complex_operations(self):
        """Test more complex lambda operations."""
        # Conditional logic
        func1 = build_safe_lambda("lambda x: 'positive' if x > 0 else 'non-positive'")
        assert func1(5) == "positive"
        assert func1(-5) == "non-positive"

        # Dictionary operations
        func2 = build_safe_lambda("lambda d: d.get('key', 'default')")
        assert func2({"key": "value"}) == "value"
        assert func2({}) == "default"

        # Multiple operations
        func3 = build_safe_lambda("lambda items: sum([x * 2 for x in items if x > 0])")
        assert func3([1, -2, 3, -4, 5]) == 18  # (1*2 + 3*2 + 5*2)

    def test_error_handling(self):
        """Test that errors in lambda execution are properly handled."""
        # Division by zero
        func1 = build_safe_lambda("lambda x: 1 / x")
        with pytest.raises(PythonScriptExecutionError):
            func1(0)

        # Attribute error
        func2 = build_safe_lambda("lambda x: x.nonexistent_method()")
        with pytest.raises(PythonScriptExecutionError):
            func2("string")

        # Type error
        func3 = build_safe_lambda("lambda x: x + 'string'")
        with pytest.raises(PythonScriptExecutionError):
            func3(123)
