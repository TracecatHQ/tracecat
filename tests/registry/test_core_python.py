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
    async def test_script_with_inputs(self, mock_logger):
        """Test script with input data."""
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
    async def test_complex_data_types(self):
        """Test handling of complex data types."""
        script_content = """
def main():
    # Return a complex data structure
    return {
        "numbers": [1, 2, 3, 4, 5],
        "dict": {"a": 1, "b": 2},
        "nested": {
            "list": [{"x": 1}, {"y": 2}],
            "tuple": (1, 2, 3)
        }
    }
"""
        result = await run_python(script=script_content)
        assert isinstance(result, dict)
        assert result["numbers"] == [1, 2, 3, 4, 5]
        assert result["dict"] == {"a": 1, "b": 2}
        assert result["nested"]["list"] == [{"x": 1}, {"y": 2}]
        # Note: tuples might be converted to lists in JSON serialization

    @pytest.mark.anyio
    async def test_clean_error_messages(self):
        """Test that error messages are clean and user-friendly without tracebacks."""
        # Test ValueError
        script_with_value_error = """
def main():
    raise ValueError("Invalid input provided")
    return "Should not reach here"
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_with_value_error)
        error_msg = str(exc_info.value)
        assert "ValueError: Invalid input provided" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

        # Test TypeError
        script_with_type_error = """
def main():
    result = "hello" + 5  # This will cause a TypeError
    return result
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_with_type_error)
        error_msg = str(exc_info.value)
        assert "TypeError" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

    @pytest.mark.anyio
    async def test_numpy_error_messages(self):
        """Test that numpy errors are extracted cleanly without complex tracebacks."""
        # Test numpy shape mismatch error
        script_with_numpy_error = """
def main():
    import numpy as np
    a = np.array([1, 2, 3])
    b = np.array([[1, 2], [3, 4]])
    # This will cause a ValueError with numpy-specific details
    result = a + b
    return result
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_with_numpy_error, dependencies=["numpy"])
        error_msg = str(exc_info.value)
        assert "ValueError" in error_msg
        # Should contain the actual numpy error message
        assert "operands could not be broadcast" in error_msg or "shape" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

        # Test numpy index error
        script_with_index_error = """
def main():
    import numpy as np
    arr = np.array([1, 2, 3])
    # This will cause an IndexError
    return arr[10]
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_with_index_error, dependencies=["numpy"])
        error_msg = str(exc_info.value)
        assert "IndexError" in error_msg
        assert "index" in error_msg.lower()
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

    @pytest.mark.anyio
    async def test_automation_libraries_success(self):
        """Test successful operations with common automation libraries."""

        # Test pandas DataFrame creation and basic operations
        pandas_script = """
def main():
    import pandas as pd
    df = pd.DataFrame({
        'name': ['Alice', 'Bob', 'Charlie'],
        'age': [25, 30, 35],
        'city': ['NYC', 'LA', 'Chicago']
    })
    return {
        'shape': df.shape,
        'columns': list(df.columns),
        'first_row': df.iloc[0].to_dict()
    }
"""
        result = await run_python(script=pandas_script, dependencies=["pandas"])
        assert result["shape"] == [3, 3]
        assert result["columns"] == ["name", "age", "city"]
        assert result["first_row"]["name"] == "Alice"

        # Test BeautifulSoup HTML parsing
        bs_script = """
def main():
    from bs4 import BeautifulSoup
    html = '<div class="content"><p>Hello World</p><p>Second paragraph</p></div>'
    soup = BeautifulSoup(html, 'html.parser')
    return {
        'text': soup.get_text().strip(),
        'p_count': len(soup.find_all('p')),
        'first_p': soup.find('p').text
    }
"""
        result = await run_python(script=bs_script, dependencies=["beautifulsoup4"])
        assert "Hello World" in result["text"]
        assert result["p_count"] == 2
        assert result["first_p"] == "Hello World"

    @pytest.mark.anyio
    async def test_automation_libraries_errors(self):
        """Test error handling with common automation libraries."""

        # Test pandas KeyError (common in data processing)
        pandas_error_script = """
def main():
    import pandas as pd
    df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})
    # This will cause a KeyError
    return df['nonexistent_column'].tolist()
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=pandas_error_script, dependencies=["pandas"])
        error_msg = str(exc_info.value)
        assert "KeyError" in error_msg
        assert "nonexistent_column" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

        # Test pandas ValueError (data type issues)
        pandas_value_error_script = """
def main():
    import pandas as pd
    df = pd.DataFrame({'text': ['hello', 'world', 'test']})
    # This will cause a ValueError when trying to convert text to numeric
    return pd.to_numeric(df['text']).tolist()
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=pandas_value_error_script, dependencies=["pandas"])
        error_msg = str(exc_info.value)
        assert "ValueError" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

        # Test BeautifulSoup AttributeError (common when element not found)
        bs_error_script = """
def main():
    from bs4 import BeautifulSoup
    html = '<div>No paragraphs here</div>'
    soup = BeautifulSoup(html, 'html.parser')
    # This will cause an AttributeError because find() returns None
    return soup.find('p').text
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=bs_error_script, dependencies=["beautifulsoup4"])
        error_msg = str(exc_info.value)
        assert "AttributeError" in error_msg
        assert "NoneType" in error_msg or "None" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback

    @pytest.mark.anyio
    async def test_excel_operations(self):
        """Test Excel operations with openpyxl - common in business automation."""

        # Test creating Excel data structure (can't actually write files in sandbox)
        excel_script = """
def main():
    # Simulate Excel-like data processing that's common in automation
    data = [
        ['Name', 'Department', 'Salary'],
        ['Alice', 'Engineering', 75000],
        ['Bob', 'Sales', 65000],
        ['Charlie', 'Marketing', 70000]
    ]

    # Process like you would with openpyxl
    headers = data[0]
    rows = data[1:]

    # Calculate average salary (common automation task)
    salaries = [row[2] for row in rows]
    avg_salary = sum(salaries) / len(salaries)

    return {
        'headers': headers,
        'row_count': len(rows),
        'average_salary': avg_salary,
        'departments': list(set(row[1] for row in rows))
    }
"""
        result = await run_python(script=excel_script)
        assert result["headers"] == ["Name", "Department", "Salary"]
        assert result["row_count"] == 3
        assert result["average_salary"] == 70000.0
        assert len(result["departments"]) == 3

    @pytest.mark.anyio
    async def test_json_processing_errors(self):
        """Test JSON processing errors common in API automation."""

        # Test JSON decode error (common when APIs return malformed data)
        json_error_script = """
def main():
    import json
    # Malformed JSON that might come from an API
    bad_json = '{"name": "test", "value": }'  # Missing value
    return json.loads(bad_json)
"""
        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=json_error_script)
        error_msg = str(exc_info.value)
        assert "JSONDecodeError" in error_msg or "ValueError" in error_msg
        assert "File " not in error_msg  # No file paths
        assert "Traceback" not in error_msg  # No traceback


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
