import shutil
from unittest.mock import patch

import pytest

from registry.tracecat_registry.core.python import (
    PythonScriptExecutionError,
    PythonScriptTimeoutError,
    PythonScriptValidationError,
    run_python,
)

# Check if Deno is available (required for the subprocess fallback)
DENO_AVAILABLE = shutil.which("deno") is not None

# Skip all tests if Deno isn't available
pytestmark = pytest.mark.skipif(
    not DENO_AVAILABLE,
    reason="Deno not available. Required for Pyodide subprocess execution.",
)


@pytest.fixture
def mock_logger():
    with patch("registry.tracecat_registry.core.python.logger") as mock_log:
        yield mock_log


class TestPythonExecution:
    """Test suite for Python execution via Deno subprocess."""

    @pytest.mark.anyio
    async def test_basic_script_execution(self, mock_logger):
        """Test basic script execution with Deno subprocess."""
        script_content = """
def main():
    print('Hello from Deno subprocess')
    value = 10 * 2
    return value
"""
        result = await run_python(script=script_content)
        assert result == 20
        mock_logger.info.assert_any_call("Script stdout:\nHello from Deno subprocess")

    @pytest.mark.anyio
    async def test_script_with_function_arguments(self, mock_logger):
        """Test script with function arguments."""
        script_content = """
def process_item(item_name, qty, price):
    print(f'Processing: {item_name}, quantity: {qty}')
    return qty * price
"""
        inputs_data = {"item_name": "TestItem", "qty": 10, "price": 2.5}
        expected_result = 25.0

        result = await run_python(script=script_content, inputs=inputs_data)
        assert result == expected_result
        mock_logger.info.assert_any_call(
            f"Script stdout:\nProcessing: {inputs_data['item_name']}, quantity: {inputs_data['qty']}"
        )

    @pytest.mark.anyio
    async def test_script_with_partial_arguments(self, mock_logger):
        """Test script with some arguments missing (should get None)."""
        script_content = """
def process_data(a, b, c):
    print(f'a={a}, b={b}, c={c}')
    # Handle None values gracefully
    result = (a or 0) + (b or 0) + (c or 0)
    return result
"""
        inputs_data = {"a": 10, "b": 5}  # c is missing, should be None
        expected_result = 15  # 10 + 5 + 0

        result = await run_python(script=script_content, inputs=inputs_data)
        assert result == expected_result
        mock_logger.info.assert_any_call("Script stdout:\na=10, b=5, c=None")

    @pytest.mark.anyio
    async def test_script_validation(self):
        """Test script validation."""
        # No function
        script_with_no_function = """
x = 10
print("This script has no function")
x * 2
"""
        with pytest.raises(PythonScriptValidationError) as exc_info:
            await run_python(script=script_with_no_function)
        assert "must contain at least one function" in str(exc_info.value)

        # Multiple functions with no main
        script_with_multiple_functions = """
def func1():
    return "Function 1"

def func2():
    return "Function 2"
"""
        with pytest.raises(PythonScriptValidationError) as exc_info:
            await run_python(script=script_with_multiple_functions)
        assert "one must be named 'main'" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_main_function_with_arguments(self):
        """Test main function with arguments when multiple functions exist."""
        script_content = """
def helper_function(x):
    return x * 2

def main(value):
    result = helper_function(value)
    return result + 10
"""
        inputs_data = {"value": 5}
        expected_result = 20  # (5 * 2) + 10

        result = await run_python(script=script_content, inputs=inputs_data)
        assert result == expected_result

    @pytest.mark.anyio
    @pytest.mark.skip(reason="Dependencies might not be available in all environments")
    async def test_script_with_dependencies(self, mock_logger):
        """Test script with dependencies."""
        # This test requires network access and might take longer
        script_content = """
def main():
    import numpy as np
    return np.array([1, 2, 3]).sum()
"""
        result = await run_python(
            script=script_content,
            dependencies=["numpy"],
            timeout_seconds=60,  # Give more time for package installation
            allow_network=True,  # Enable network access for package installation
        )
        assert result == 6

    @pytest.mark.anyio
    async def test_script_error_handling(self):
        """Test script error handling."""
        script_with_error = """
def main():
    raise ValueError("This is a test error")
    return "Should not reach here"
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_with_error)
        assert "This is a test error" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_script_timeout(self):
        """Test script timeout."""
        script_with_infinite_loop = """
def main():
    import time
    while True:
        time.sleep(0.1)
    return "Should not reach here"
"""
        with pytest.raises(PythonScriptTimeoutError) as exc_info:
            await run_python(script=script_with_infinite_loop, timeout_seconds=1)
        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_complex_data_types_with_arguments(self):
        """Test handling of complex data types as function arguments."""
        script_content = """
def main(data_list, data_dict):
    # Return a complex data structure
    return {
        "input_list": data_list,
        "input_dict": data_dict,
        "processed": {
            "list_length": len(data_list),
            "dict_keys": list(data_dict.keys())
        }
    }
"""
        inputs_data = {"data_list": [1, 2, 3, 4, 5], "data_dict": {"a": 1, "b": 2}}

        result = await run_python(script=script_content, inputs=inputs_data)
        assert isinstance(result, dict)
        assert result["input_list"] == [1, 2, 3, 4, 5]
        assert result["input_dict"] == {"a": 1, "b": 2}
        assert result["processed"]["list_length"] == 5
        assert result["processed"]["dict_keys"] == ["a", "b"]

    @pytest.mark.anyio
    async def test_clean_error_messages(self):
        """Test that error messages are clean and user-friendly without tracebacks."""
        # Test ValueError with function arguments
        script_with_value_error = """
def main(value):
    if value is None:
        raise ValueError("Invalid input provided")
    return value * 2
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_with_value_error, inputs={"value": None})
        error_msg = str(exc_info.value)
        assert "ValueError: Invalid input provided" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

        # Test TypeError with function arguments
        script_with_type_error = """
def main(a, b):
    result = a + b  # This will cause a TypeError if types are incompatible
    return result
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(
                script=script_with_type_error, inputs={"a": "hello", "b": 5}
            )
        error_msg = str(exc_info.value)
        assert "TypeError" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

    @pytest.mark.anyio
    async def test_numpy_error_messages(self):
        """Test that numpy errors are extracted cleanly without complex tracebacks."""
        # Test numpy shape mismatch error with function arguments
        script_with_numpy_error = """
def main(array_a, array_b):
    import numpy as np
    a = np.array(array_a)
    b = np.array(array_b)
    # This will cause a ValueError with numpy-specific details
    result = a + b
    return result.tolist()
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(
                script=script_with_numpy_error,
                inputs={"array_a": [1, 2, 3], "array_b": [[1, 2], [3, 4]]},
                dependencies=["numpy"],
                allow_network=True,  # Enable network access for package installation
            )
        error_msg = str(exc_info.value)
        assert "ValueError" in error_msg
        # Should contain the actual numpy error message
        assert "operands could not be broadcast" in error_msg or "shape" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

    @pytest.mark.anyio
    async def test_automation_libraries_success(self):
        """Test successful operations with common automation libraries using function arguments."""

        # Test pandas DataFrame creation and basic operations
        pandas_script = """
def main(sales_data):
    import pandas as pd
    df = pd.DataFrame(sales_data)
    return {
        'shape': list(df.shape),
        'columns': list(df.columns),
        'first_row': df.iloc[0].to_dict()
    }
"""
        sales_data = [
            {"name": "Alice", "age": 25, "city": "NYC"},
            {"name": "Bob", "age": 30, "city": "LA"},
            {"name": "Charlie", "age": 35, "city": "Chicago"},
        ]
        result = await run_python(
            script=pandas_script,
            inputs={"sales_data": sales_data},
            dependencies=["pandas"],
            allow_network=True,  # Enable network access for package installation
        )
        assert result["shape"] == [3, 3]
        assert result["columns"] == ["name", "age", "city"]
        assert result["first_row"]["name"] == "Alice"

        # Test BeautifulSoup HTML parsing
        bs_script = """
def main(html_content):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    return {
        'text': soup.get_text().strip(),
        'p_count': len(soup.find_all('p')),
        'first_p': soup.find('p').text
    }
"""
        html_content = (
            '<div class="content"><p>Hello World</p><p>Second paragraph</p></div>'
        )
        result = await run_python(
            script=bs_script,
            inputs={"html_content": html_content},
            dependencies=["beautifulsoup4"],
            allow_network=True,  # Enable network access for package installation
        )
        assert "Hello World" in result["text"]
        assert result["p_count"] == 2
        assert result["first_p"] == "Hello World"

    @pytest.mark.anyio
    async def test_automation_libraries_errors(self):
        """Test error handling with common automation libraries using function arguments."""

        # Test pandas KeyError (common in data processing)
        pandas_error_script = """
def main(data):
    import pandas as pd
    df = pd.DataFrame(data)
    # This will cause a KeyError
    return df['nonexistent_column'].tolist()
"""
        data = [{"a": 1, "b": 4}, {"a": 2, "b": 5}, {"a": 3, "b": 6}]
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(
                script=pandas_error_script,
                inputs={"data": data},
                dependencies=["pandas"],
                allow_network=True,  # Enable network access for package installation
            )
        error_msg = str(exc_info.value)
        assert "KeyError" in error_msg
        assert "nonexistent_column" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

        # Test BeautifulSoup AttributeError (common when element not found)
        bs_error_script = """
def main(html_content):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    # This will cause an AttributeError because find() returns None
    return soup.find('p').text
"""
        html_content = "<div>No paragraphs here</div>"
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(
                script=bs_error_script,
                inputs={"html_content": html_content},
                dependencies=["beautifulsoup4"],
                allow_network=True,  # Enable network access for package installation
            )
        error_msg = str(exc_info.value)
        assert "AttributeError" in error_msg
        assert "NoneType" in error_msg or "None" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback


class TestDocumentationExamples:
    """Test suite for examples from the documentation to ensure they work correctly."""

    @pytest.mark.anyio
    async def test_doc_example_function_no_inputs(self):
        """Test the 'Function (no inputs)' example from the documentation."""
        script = """
def main():
    # Simple arithmetic
    result = 10 * 5 + 3
    return result
"""
        result = await run_python(script=script)
        assert result == 53

    @pytest.mark.anyio
    async def test_doc_example_function_with_inputs(self):
        """Test the 'Function (with inputs)' example from the documentation."""
        script = """
def main(a, b):
    # Simple arithmetic
    result = a * b + 3
    return result
"""
        inputs = {"a": 10, "b": 5}
        result = await run_python(script=script, inputs=inputs)
        assert result == 53

    @pytest.mark.anyio
    async def test_doc_example_multiple_functions(self):
        """Test the 'Multiple functions' example from the documentation."""
        script = """
def calculate_tax(amount, rate):
    return amount * rate

def main(subtotal, tax_rate):
    # When multiple functions exist, 'main' is called
    tax = calculate_tax(subtotal, tax_rate)
    return subtotal + tax
"""
        inputs = {"subtotal": 100.0, "tax_rate": 0.08}
        result = await run_python(script=script, inputs=inputs)
        assert result == 108.0

    @pytest.mark.anyio
    @pytest.mark.skip(reason="Dependencies might not be available in all environments")
    async def test_doc_example_with_pip_packages(self):
        """Test the 'With pip packages' example from the documentation."""
        script = """
def main():
    import numpy as np
    result = np.array([1, 2, 3])
    return result.tolist()
"""
        dependencies = ["numpy"]
        result = await run_python(
            script=script,
            dependencies=dependencies,
            allow_network=True,  # Enable network access for package installation
        )
        assert result == [1, 2, 3]


class TestNetworkSecurity:
    """Test suite for network access security controls."""

    @pytest.mark.anyio
    async def test_network_disabled_by_default(self):
        """Test that network access is disabled by default when using dependencies."""
        script = """
def main():
    import numpy as np
    return np.array([1, 2, 3]).sum()
"""
        # Should fail because numpy can't be downloaded without network access
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script, dependencies=["numpy"])

        error_msg = str(exc_info.value)
        assert "ModuleNotFoundError" in error_msg
        assert "numpy" in error_msg

    @pytest.mark.anyio
    async def test_basic_scripts_work_without_network(self):
        """Test that basic scripts work fine without network access."""
        script = """
def main(x, y):
    # Basic operations that don't require external packages
    result = x * y + 10
    return result
"""
        # Should work fine without network access
        result = await run_python(script=script, inputs={"x": 5, "y": 3})
        assert result == 25

    @pytest.mark.anyio
    async def test_builtin_modules_work_without_network(self):
        """Test that Python builtin modules work without network access."""
        script = """
def main():
    import json
    import math
    import datetime

    data = {"value": math.pi, "timestamp": str(datetime.datetime.now())}
    return json.dumps(data)
"""
        # Should work fine with builtin modules
        result = await run_python(script=script)
        assert isinstance(result, str)
        # Should be valid JSON
        import json

        parsed = json.loads(result)
        assert "value" in parsed
        assert "timestamp" in parsed

    @pytest.mark.anyio
    @pytest.mark.skip(reason="Network test - may be slow and requires internet access")
    async def test_network_enabled_allows_dependencies(self):
        """Test that dependencies work when network access is explicitly enabled."""
        script = """
def main():
    import numpy as np
    return np.array([1, 2, 3]).sum()
"""
        # Should work when network access is enabled
        result = await run_python(
            script=script,
            dependencies=["numpy"],
            allow_network=True,
            timeout_seconds=60,  # Give more time for package download
        )
        assert result == 6

    @pytest.mark.anyio
    async def test_empty_dependencies_work_without_network(self):
        """Test that empty dependencies list works without network access."""
        script = """
def main():
    return "Hello, World!"
"""
        # Should work fine with empty dependencies
        result = await run_python(script=script, dependencies=[])
        assert result == "Hello, World!"

    @pytest.mark.anyio
    async def test_none_dependencies_work_without_network(self):
        """Test that None dependencies work without network access."""
        script = """
def main():
    return 42
"""
        # Should work fine with None dependencies
        result = await run_python(script=script, dependencies=None)
        assert result == 42


class TestInputValidation:
    """Test suite for input validation to ensure only dict inputs are accepted."""

    @pytest.mark.anyio
    async def test_dict_inputs_work_correctly(self):
        """Test that dictionary inputs work correctly as function arguments."""
        script = """
def main(name, age, city):
    return f'{name} is {age} years old and lives in {city}'
"""
        inputs = {"name": "Alice", "age": 30, "city": "NYC"}
        result = await run_python(script=script, inputs=inputs)
        assert result == "Alice is 30 years old and lives in NYC"

    @pytest.mark.anyio
    async def test_nested_dict_inputs_work(self):
        """Test that nested dictionary values work as function arguments."""
        script = """
def main(user_data, settings):
    return f"User: {user_data['name']}, Theme: {settings['theme']}"
"""
        inputs = {
            "user_data": {"name": "Bob", "age": 25},
            "settings": {"theme": "dark", "lang": "en"},
        }
        result = await run_python(script=script, inputs=inputs)
        assert result == "User: Bob, Theme: dark"

    @pytest.mark.anyio
    async def test_complex_data_types_as_inputs(self):
        """Test that complex data types work correctly as function arguments."""
        script = """
def main(numbers, metadata):
    total = sum(numbers)
    return {
        'sum': total,
        'count': len(numbers),
        'source': metadata['source']
    }
"""
        inputs = {
            "numbers": [1, 2, 3, 4, 5],
            "metadata": {"source": "test_data", "version": "1.0"},
        }
        result = await run_python(script=script, inputs=inputs)
        assert result["sum"] == 15
        assert result["count"] == 5
        assert result["source"] == "test_data"

    @pytest.mark.anyio
    async def test_none_inputs_work(self):
        """Test that None inputs work (no arguments passed)."""
        script = """
def main():
    return "No inputs needed"
"""
        result = await run_python(script=script, inputs=None)
        assert result == "No inputs needed"

    @pytest.mark.anyio
    async def test_empty_dict_inputs_work(self):
        """Test that empty dictionary inputs work."""
        script = """
def main():
    return "Empty dict inputs"
"""
        result = await run_python(script=script, inputs={})
        assert result == "Empty dict inputs"

    @pytest.mark.anyio
    async def test_missing_function_parameters_get_none(self):
        """Test that missing function parameters get None values."""
        script = """
def main(required_param, optional_param):
    if optional_param is None:
        return f"Required: {required_param}, Optional: missing"
    else:
        return f"Required: {required_param}, Optional: {optional_param}"
"""
        # Only provide required_param, optional_param should get None
        inputs = {"required_param": "test_value"}
        result = await run_python(script=script, inputs=inputs)
        assert result == "Required: test_value, Optional: missing"

    # Note: We can't test non-dict inputs at runtime because the type system
    # prevents it at the function signature level. The type annotation
    # `inputs: dict[str, Any] | None` ensures only dict or None can be passed.
    # This is enforced by the type checker and would cause a TypeError if
    # someone tried to pass a list or other non-dict type.


class TestSingleFunctionInputs:
    """Test suite to verify inputs work with single functions not named 'main'."""

    @pytest.mark.anyio
    async def test_single_function_not_named_main_with_inputs(self):
        """Test that inputs work with a single function not named 'main'."""
        script = """
def process_order(item_name, quantity, price_per_item):
    total_cost = quantity * price_per_item
    return f"Order: {quantity}x {item_name} = ${total_cost:.2f}"
"""
        inputs = {"item_name": "Widget", "quantity": 3, "price_per_item": 15.99}
        result = await run_python(script=script, inputs=inputs)
        assert result == "Order: 3x Widget = $47.97"

    @pytest.mark.anyio
    async def test_single_function_no_params_no_inputs(self):
        """Test that a single function with no parameters works without inputs."""
        script = """
def calculate_pi_approximation():
    # Simple approximation
    return 22 / 7
"""
        result = await run_python(script=script)
        assert abs(result - 3.142857142857143) < 0.000001

    @pytest.mark.anyio
    async def test_single_function_with_missing_inputs(self):
        """Test that missing inputs get None for single functions."""
        script = """
def greet_user(name, title):
    if title is None:
        return f"Hello, {name}!"
    else:
        return f"Hello, {title} {name}!"
"""
        inputs = {"name": "Alice"}  # title is missing, should get None
        result = await run_python(script=script, inputs=inputs)
        assert result == "Hello, Alice!"

    @pytest.mark.anyio
    async def test_main_function_takes_precedence(self):
        """Test that 'main' function is called even when other functions exist."""
        script = """
def helper_function(x):
    return x * 2

def main(value):
    # This should be called, not helper_function
    return helper_function(value) + 10
"""
        inputs = {"value": 5}
        result = await run_python(script=script, inputs=inputs)
        assert result == 20  # (5 * 2) + 10

    @pytest.mark.anyio
    async def test_extra_input_fields_are_ignored(self):
        """Test that extra input fields not matching function parameters are silently ignored."""
        script = """
def main(name, age):
    return f"{name} is {age} years old"
"""
        inputs = {
            "name": "Alice",
            "age": 30,
            "city": "NYC",  # This extra field should be ignored
            "country": "USA",  # This extra field should be ignored
            "occupation": "Engineer",  # This extra field should be ignored
        }
        result = await run_python(script=script, inputs=inputs)
        assert result == "Alice is 30 years old"


def print_deno_installation_instructions():
    """Print instructions for installing Deno."""
    print("\n" + "=" * 80)
    print("Deno Installation Instructions")
    print("=" * 80)
    print("""
To run these tests, you need Deno installed in your environment:

1. Install Deno:
   - macOS/Linux: curl -fsSL https://deno.land/install.sh | sh
   - Windows: iwr https://deno.land/install.ps1 -useb | iex
   - Alternative: brew install deno (macOS with Homebrew)

2. Add Deno to your PATH if it's not already added

3. Run the tests with:
   python -m pytest tests/registry/test_core_python.py -v

Note: Some tests require network access for downloading Pyodide.
""")
    print("=" * 80 + "\n")


# Print installation instructions if Deno is not available
if not DENO_AVAILABLE and __name__ == "__main__":
    print_deno_installation_instructions()
