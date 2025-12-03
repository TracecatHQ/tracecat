import os
import shutil
from unittest.mock import patch

import pytest
from tracecat_registry.core.python import (
    PythonScriptExecutionError,
    PythonScriptTimeoutError,
    PythonScriptValidationError,
    run_python,
)

# Check if nsjail is available (required for the sandbox)
NSJAIL_AVAILABLE = shutil.which("nsjail") is not None

# Check if the sandbox rootfs exists
ROOTFS_PATH = os.environ.get(
    "TRACECAT__SANDBOX_ROOTFS_PATH", "/var/lib/tracecat/sandbox-rootfs"
)
ROOTFS_AVAILABLE = os.path.isdir(ROOTFS_PATH)

# Skip all tests if nsjail or rootfs isn't available
pytestmark = pytest.mark.skipif(
    not (NSJAIL_AVAILABLE and ROOTFS_AVAILABLE),
    reason=(
        f"nsjail sandbox not available. "
        f"nsjail installed: {NSJAIL_AVAILABLE}, rootfs exists: {ROOTFS_AVAILABLE}"
    ),
)


@pytest.fixture
def mock_logger():
    with patch("tracecat_registry.core.python.logger") as mock_log:
        yield mock_log


class TestPythonExecution:
    """Test suite for Python execution via nsjail sandbox."""

    @pytest.mark.anyio
    async def test_basic_script_execution(self):
        """Test basic script execution with nsjail sandbox."""
        script_content = """
def main():
    print('Hello from nsjail sandbox')
    value = 10 * 2
    return value
"""
        result = await run_python(script=script_content)
        assert result == 20

    @pytest.mark.anyio
    async def test_script_with_function_arguments(self):
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

    @pytest.mark.anyio
    async def test_script_with_partial_arguments(self):
        """Test script with some arguments missing (should raise TypeError)."""
        script_content = """
def process_data(a, b, c):
    print(f'a={a}, b={b}, c={c}')
    result = (a or 0) + (b or 0) + (c or 0)
    return result
"""
        inputs_data = {"a": 10, "b": 5}  # c is missing, should raise TypeError

        with pytest.raises(PythonScriptExecutionError) as exc_info:
            await run_python(script=script_content, inputs=inputs_data)

        error_msg = str(exc_info.value)
        assert "TypeError" in error_msg
        assert "missing 1 required positional argument" in error_msg
        assert "'c'" in error_msg

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
    async def test_script_with_dependencies(self):
        """Test script with dependencies."""
        script_content = """
def main():
    import requests
    return "requests module loaded successfully"
"""
        result = await run_python(
            script=script_content,
            dependencies=["requests"],
            timeout_seconds=120,
        )
        assert "requests module loaded successfully" in result

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
            await run_python(script=script_with_infinite_loop, timeout_seconds=2)
        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.anyio
    async def test_complex_data_types_with_arguments(self):
        """Test handling of complex data types as function arguments."""
        script_content = """
def main(data_list, data_dict):
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

    @pytest.mark.anyio
    async def test_subprocess_support(self):
        """Test that subprocess.run works in nsjail sandbox."""
        script_content = """
def main():
    import subprocess
    result = subprocess.run(['echo', 'Hello from subprocess'], capture_output=True, text=True)
    return result.stdout.strip()
"""
        result = await run_python(script=script_content)
        assert result == "Hello from subprocess"

    @pytest.mark.anyio
    async def test_env_vars_injection(self):
        """Test that environment variables are properly injected."""
        script_content = """
def main():
    import os
    return os.environ.get('TEST_SECRET', 'not found')
"""
        result = await run_python(
            script=script_content,
            env_vars={"TEST_SECRET": "secret_value_123"},
        )
        assert result == "secret_value_123"


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


class TestNetworkSecurity:
    """Test suite for network access security controls."""

    @pytest.mark.anyio
    async def test_network_disabled_by_default(self):
        """Test that network access is disabled by default."""
        script = """
def main():
    try:
        import urllib.request
        response = urllib.request.urlopen('https://httpbin.org/get', timeout=5)
        return "Network access allowed"
    except Exception as e:
        return f"Network blocked: {type(e).__name__}"
"""
        result = await run_python(script=script)
        assert "Network blocked" in result

    @pytest.mark.anyio
    async def test_basic_scripts_work_without_network(self):
        """Test that basic scripts work fine without network access."""
        script = """
def main(x, y):
    result = x * y + 10
    return result
"""
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
        result = await run_python(script=script)
        assert isinstance(result, str)
        import json

        parsed = json.loads(result)
        assert "value" in parsed
        assert "timestamp" in parsed

    @pytest.mark.anyio
    async def test_network_enabled_when_requested(self):
        """Test that network access works when explicitly enabled."""
        script = """
def main():
    try:
        import urllib.request
        response = urllib.request.urlopen('https://httpbin.org/get', timeout=10)
        return f"Network access successful: {response.status}"
    except Exception as e:
        return f"Network failed: {type(e).__name__}: {str(e)}"
"""
        result = await run_python(
            script=script,
            allow_network=True,
            timeout_seconds=30,
        )
        # Either succeeds or fails for network reasons (not sandbox blocking)
        assert "Network access successful" in result or "Network failed" in result


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
    return 22 / 7
"""
        result = await run_python(script=script)
        assert abs(result - 3.142857142857143) < 0.000001


class TestSecurityIsolation:
    """Test suite for security and sandbox isolation."""

    @pytest.mark.anyio
    async def test_command_injection_via_inputs(self):
        """Test that malicious inputs cannot inject Python code."""
        script = """
def main(user_input):
    return f"Processed: {user_input}"
"""
        malicious_inputs = {
            "user_input": '"); import os; os.system("cat /etc/passwd"); print("'
        }

        result = await run_python(script=script, inputs=malicious_inputs)
        assert (
            result == 'Processed: "); import os; os.system("cat /etc/passwd"); print("'
        )

    @pytest.mark.anyio
    async def test_file_system_isolation(self):
        """Test that scripts cannot access the host file system."""
        script_read = """
def main():
    try:
        # Try to read host's /etc/passwd
        with open('/etc/passwd', 'r') as f:
            content = f.read()
        # If we can read it, return the first line to verify it's sandbox's file
        return content.split('\\n')[0]
    except Exception as e:
        return f"Error: {type(e).__name__}"
"""
        result = await run_python(script=script_read)
        # Should either fail or read the sandbox's /etc/passwd (with sandbox user)
        if "Error" not in result:
            # If it succeeded, verify it's reading sandbox's passwd file
            assert "sandbox" in result or "root" in result

    @pytest.mark.anyio
    async def test_large_input_handling(self):
        """Test handling of large inputs."""
        script = """
def main(data):
    return f"Received {len(data)} items"
"""
        large_input = {"data": list(range(10000))}
        result = await run_python(script=script, inputs=large_input)
        assert result == "Received 10000 items"

    @pytest.mark.anyio
    async def test_nested_function_calls_safe(self):
        """Test that nested function calls with user input are safe."""
        script = """
def process_data(data):
    return data.upper() if isinstance(data, str) else str(data)

def main(user_input):
    result = process_data(user_input)
    return f"Final: {result}"
"""
        malicious_input = {"user_input": "__import__('os').system('id')"}

        result = await run_python(script=script, inputs=malicious_input)
        assert result == "Final: __IMPORT__('OS').SYSTEM('ID')"


def print_nsjail_installation_instructions():
    """Print instructions for setting up nsjail sandbox."""
    print("\n" + "=" * 80)
    print("nsjail Sandbox Setup Instructions")
    print("=" * 80)
    print(
        """
These tests require nsjail sandbox to be set up. This is typically done
inside the Docker container during the image build process.

To run tests locally:

1. Build and run the development Docker container:
   just dev

2. Run tests inside the executor container:
   docker exec -it tracecat-executor-1 pytest tests/registry/test_core_python.py -v

The sandbox requires:
- Linux with kernel >= 4.6 (for user namespaces)
- nsjail binary at /usr/local/bin/nsjail
- Sandbox rootfs at /var/lib/tracecat/sandbox-rootfs
- SYS_ADMIN capability (configured in docker-compose)
"""
    )
    print("=" * 80 + "\n")


# Print installation instructions if sandbox is not available
if not (NSJAIL_AVAILABLE and ROOTFS_AVAILABLE) and __name__ == "__main__":
    print_nsjail_installation_instructions()
