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
    async def test_backward_compatibility_no_params(self, mock_logger):
        """Test backward compatibility with functions that have no parameters (global variables)."""
        script_content = """
def process_item():
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
        result = await run_python(script=script, dependencies=dependencies)
        assert result == [1, 2, 3]


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
