import pytest

from tracecat.expressions.common import build_safe_lambda


class TestBuildSafeLambdaSecurity:
    """Test security features of build_safe_lambda function."""

    def test_blocks_dunder_method_access(self):
        """Test that access to dunder methods is blocked."""
        dangerous_exprs = [
            "lambda x: x.__class__",
            "lambda x: x.__dict__",
            "lambda x: x.__module__",
            "lambda x: x.__bases__",
            "lambda x: x.__subclasses__()",
            "lambda x: x.__globals__",
            "lambda x: x.__builtins__",
            "lambda x: x.__import__",
        ]

        for expr in dangerous_exprs:
            with pytest.raises(
                ValueError, match="dangerous pattern|restricted attribute|magic method"
            ):
                build_safe_lambda(expr)

    def test_blocks_getattr_setattr_variants(self):
        """Test that getattr, setattr, hasattr, delattr are blocked."""
        dangerous_exprs = [
            'lambda x: getattr(x, "__class__")',
            'lambda x: setattr(x, "attr", "value")',
            'lambda x: hasattr(x, "__class__")',
            'lambda x: delattr(x, "attr")',
            'lambda x: getattr(x, chr(95)+chr(95)+"class"+chr(95)+chr(95))',
        ]

        for expr in dangerous_exprs:
            with pytest.raises(ValueError, match="dangerous pattern|restricted"):
                build_safe_lambda(expr)

    def test_blocks_import_variants(self):
        """Test that various import mechanisms are blocked."""
        dangerous_exprs = [
            'lambda x: __import__("os")',
            'lambda x: __import__("sys").exit()',
            "lambda x: import os",  # This would be a syntax error anyway
        ]

        for expr in dangerous_exprs:
            with pytest.raises(ValueError, match="dangerous pattern|restricted|syntax"):
                build_safe_lambda(expr)

    def test_blocks_eval_exec_compile(self):
        """Test that eval, exec, compile are blocked."""
        dangerous_exprs = [
            'lambda x: eval("1+1")',
            'lambda x: exec("print(1)")',
            'lambda x: compile("1+1", "<string>", "eval")',
        ]

        for expr in dangerous_exprs:
            with pytest.raises(ValueError, match="dangerous pattern|restricted"):
                build_safe_lambda(expr)

    def test_blocks_file_operations(self):
        """Test that file operations are blocked."""
        dangerous_exprs = [
            'lambda x: open("/etc/passwd")',
            'lambda x: file("/etc/passwd")',
            'lambda x: input("Enter password:")',
        ]

        for expr in dangerous_exprs:
            with pytest.raises(ValueError, match="dangerous pattern|restricted"):
                build_safe_lambda(expr)

    def test_blocks_system_operations(self):
        """Test that system operations are blocked."""
        dangerous_exprs = [
            'lambda x: x.system("ls")',
            'lambda x: x.popen("ls")',
            'lambda x: x.call(["ls"])',
            'lambda x: x.run(["ls"])',
        ]

        for expr in dangerous_exprs:
            with pytest.raises(ValueError, match="dangerous pattern"):
                build_safe_lambda(expr)

    def test_blocks_introspection_functions(self):
        """Test that introspection functions are blocked."""
        dangerous_exprs = [
            "lambda x: dir(x)",
            "lambda x: vars(x)",
            "lambda x: type(x)",
            "lambda x: callable(x)",
            "lambda x: isinstance(x, str)",
            "lambda x: globals()",
            "lambda x: locals()",
        ]

        for expr in dangerous_exprs:
            with pytest.raises(ValueError, match="restricted"):
                build_safe_lambda(expr)

    def test_blocks_advanced_escape_attempts(self):
        """Test advanced Python sandbox escape techniques."""
        dangerous_exprs = [
            # Classic Python sandbox escape
            "lambda x: ().__class__.__bases__[0].__subclasses__()[104]",
            # Unicode encoding bypass attempts
            'lambda x: getattr(x, "\\u005f\\u005fclass\\u005f\\u005f")',
            # String concatenation bypasses
            'lambda x: getattr(x, "__" + "class" + "__")',
            'lambda x: x["__class__"]',
            # Method resolution bypasses
            "lambda x: x.mro()",
        ]

        for expr in dangerous_exprs:
            with pytest.raises(
                ValueError, match="dangerous pattern|restricted|magic method"
            ):
                build_safe_lambda(expr)

    def test_input_validation(self):
        """Test input validation for lambda expressions."""
        # Empty or None input
        with pytest.raises(ValueError, match="non-empty string"):
            build_safe_lambda("")

        with pytest.raises(ValueError, match="non-empty string"):
            build_safe_lambda(None)  # type: ignore

        # Too long input
        long_expr = "lambda x: " + "x + " * 500 + "1"
        with pytest.raises(ValueError, match="too long"):
            build_safe_lambda(long_expr)

        # Null bytes
        with pytest.raises(ValueError, match="null bytes"):
            build_safe_lambda("lambda x: x\x00")

    def test_lambda_structure_validation(self):
        """Test validation of lambda structure."""
        # Not a lambda
        with pytest.raises(ValueError, match="must be a lambda"):
            build_safe_lambda("x + 1")

        # No parameters
        with pytest.raises(ValueError, match="at least one parameter"):
            build_safe_lambda("lambda: 42")

        # Too many parameters
        with pytest.raises(ValueError, match="more than 3 parameters"):
            build_safe_lambda("lambda a, b, c, d: a")

    def test_allows_safe_operations(self):
        """Test that safe operations are allowed."""
        safe_exprs = [
            "lambda x: x + 1",
            "lambda x: len(x)",
            "lambda x, y: x * y",
            "lambda data: sum(data)",
            "lambda s: s.upper()",
            "lambda s: s.strip()",
            "lambda x: str(x)",
            "lambda x: int(x)",
            "lambda x: float(x)",
            "lambda x: bool(x)",
            "lambda items: list(filter(lambda x: x > 0, items))",
            "lambda data: max(data)",
            "lambda data: min(data)",
            "lambda x: abs(x)",
            "lambda items: sorted(items)",
            'lambda x: x.split(",")',
            'lambda x: x.replace("a", "b")',
        ]

        for expr in safe_exprs:
            # Should not raise an exception
            func = build_safe_lambda(expr)
            assert callable(func)

    def test_safe_lambda_execution(self):
        """Test that safe lambdas execute correctly with restricted globals."""
        # Test basic arithmetic
        func1 = build_safe_lambda("lambda x: x + 1")
        assert func1(5) == 6

        # Test string operations
        func2 = build_safe_lambda("lambda x: x.upper()")
        assert func2("hello") == "HELLO"

        # Test list operations
        func3 = build_safe_lambda("lambda x: len(x)")
        assert func3([1, 2, 3]) == 3

        # Test safe built-ins
        func4 = build_safe_lambda("lambda x: sum(x)")
        assert func4([1, 2, 3]) == 6

    def test_jsonpath_functionality(self):
        """Test that jsonpath functionality works in secure lambdas."""
        test_data = {
            "users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        }

        func = build_safe_lambda('lambda data: jsonpath("$.users[*].name", data)')
        result = func(test_data)
        assert result == ["Alice", "Bob"]

    def test_restricted_globals_environment(self):
        """Test that the execution environment has restricted globals."""
        # This lambda would normally have access to __builtins__, but shouldn't in our secure env
        func = build_safe_lambda("lambda x: x")

        # The function should work but not have access to dangerous globals
        assert func("test") == "test"

        # Test that dangerous built-ins are not available even if we try to access them
        # (This would be caught at parse time anyway, but good to verify)
        with pytest.raises(ValueError, match="dangerous pattern"):
            build_safe_lambda("lambda x: __builtins__")

    def test_comprehensive_bypass_attempts(self):
        """Test comprehensive list of known Python sandbox bypass techniques."""
        bypass_attempts = [
            # Classic escapes
            "lambda x: ().__class__.__bases__[0].__subclasses__()",
            "lambda x: [].__class__.__bases__[0].__subclasses__()",
            'lambda x: "".__class__.__mro__[1].__subclasses__()',
            # Attribute access variations
            "lambda x: x.__class__.__dict__",
            "lambda x: x.__class__.__module__",
            "lambda x: x.__class__.__name__",
            # Built-in access attempts
            "lambda x: __builtins__.__dict__",
            "lambda x: globals().__builtins__",
            # Method access attempts
            'lambda x: x.__getattribute__("__class__")',
            'lambda x: x.__getattr__("__class__")',
            # Import variations
            'lambda x: __import__("subprocess").call(["ls"])',
            'lambda x: __import__("os").system("echo pwned")',
            # Code object access
            "lambda x: x.__code__",
            "lambda x: x.__func__",
            "lambda x: x.__globals__",
        ]

        for expr in bypass_attempts:
            with pytest.raises(ValueError):
                build_safe_lambda(expr)

    def test_edge_cases_and_corner_cases(self):
        """Test edge cases that might slip through validation."""
        edge_cases = [
            # Whitespace variations
            'lambda x: getattr( x , "__class__" )',
            'lambda x:getattr(x,"__class__")',
            'lambda x: getattr\t(x, "__class__")',
            # Case variations (should be caught by case-insensitive matching)
            'lambda x: GETATTR(x, "__class__")',
            'lambda x: GetAttr(x, "__class__")',
            # Complex expressions that might hide dangerous operations
            'lambda x: [getattr(item, "__class__") for item in x]',
            'lambda x: {"class": getattr(x, "__class__")}',
        ]

        for expr in edge_cases:
            with pytest.raises(ValueError):
                build_safe_lambda(expr)
