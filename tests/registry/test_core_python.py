import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from registry.tracecat_registry.core.python import (
    _run_python_script_subprocess,
    run_python,
)


@pytest.fixture
def mock_logger():
    with patch("registry.tracecat_registry.core.python.logger") as mock_log:
        yield mock_log


class TestPythonExecution:
    """Test suite for Python execution via Pyodide WebAssembly."""

    @pytest.mark.asyncio
    async def test_run_python_basic_pyodide_env(self, mock_logger):
        """Test basic script execution in simulated Pyodide environment."""
        script_content = "print('Hello from Pyodide')\nvalue = 10 * 2\nvalue"

        # Simulate being in Pyodide env by making eval_code_async available
        with patch(
            "registry.tracecat_registry.core.python.eval_code_async",
            new_callable=AsyncMock,
        ) as mock_eval_code_async:
            mock_eval_code_async.return_value = (
                20  # Mock the return of the last expression
            )

            result = await run_python(script=script_content, inputs={"input_var": 5})

            assert result == 20
            mock_eval_code_async.assert_called_once()
            # Check globals passed to eval_code_async
            args, kwargs = mock_eval_code_async.call_args
            assert args[0] == script_content
            assert kwargs["globals"]["input_var"] == 5
            assert kwargs["globals"]["__name__"] == "__main__"

            # Check logger calls for stdout
            mock_logger.info.assert_any_call("Script stdout:\nHello from Pyodide")

    @pytest.mark.asyncio
    async def test_run_python_with_inputs_data_subprocess(self, mock_logger):
        """Test script with inputs data using subprocess fallback."""
        script_content = (
            "print(f'Processing: {item_name}, quantity: {qty}')\nqty * price"
        )
        inputs_data = {"item_name": "TestItem", "qty": 10, "price": 2.5}
        expected_result = 25.0

        # Ensure eval_code_async is not found, forcing fallback
        with patch(
            "registry.tracecat_registry.core.python.eval_code_async",
            side_effect=ImportError,
        ):
            # Mock the subprocess part
            with patch(
                "registry.tracecat_registry.core.python._run_python_script_subprocess",
                new_callable=AsyncMock,
            ) as mock_subprocess_runner:
                # Simulate successful subprocess execution
                mock_subprocess_runner.return_value = {
                    "result": expected_result,
                    "stdout": f"Processing: {inputs_data['item_name']}, quantity: {inputs_data['qty']}",
                    "stderr": "",
                    "success": True,
                    "error": None,
                }

                result = await run_python(script=script_content, inputs=inputs_data)

                assert result == expected_result
                mock_subprocess_runner.assert_called_once_with(
                    script_content,
                    inputs_data,
                    None,
                    30,  # Default packages and timeout
                )
                mock_logger.info.assert_any_call(
                    f"Script stdout:\nProcessing: {inputs_data['item_name']}, quantity: {inputs_data['qty']}"
                )

    @pytest.mark.asyncio
    async def test_run_python_script_timeout_pyodide(self, mock_logger):
        """Test script timeout in Pyodide environment."""
        script_content = "import time\ntime.sleep(5)"
        with patch(
            "registry.tracecat_registry.core.python.eval_code_async",
            new_callable=AsyncMock,
        ) as mock_eval_code_async:
            mock_eval_code_async.side_effect = asyncio.TimeoutError

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(script=script_content, timeout_seconds=1)

            assert "timed out" in str(exc_info.value).lower()
            mock_logger.error.assert_any_call(
                "Script stderr:\n"
            )  # Pyodide path might have empty stderr on timeout before script produces it
            mock_logger.error.assert_any_call(
                "Script execution failed: Script execution timed out after 1 seconds"
            )

    @pytest.mark.asyncio
    async def test_run_python_script_error_pyodide(self, mock_logger):
        """Test script error (exception) in Pyodide environment."""
        script_content = "raise ValueError('custom error')"
        with patch(
            "registry.tracecat_registry.core.python.eval_code_async",
            new_callable=AsyncMock,
        ) as mock_eval_code_async:
            mock_eval_code_async.side_effect = ValueError("custom error")

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(script=script_content)

            assert "custom error" in str(exc_info.value)
            # In this direct exception case, stdout/stderr might be empty if error is early
            mock_logger.error.assert_any_call("Script stderr:\n")
            mock_logger.error.assert_any_call("Script execution failed: custom error")

    @pytest.mark.asyncio
    async def test_run_python_dependency_loading_pyodide(self, mock_logger):
        """Test dependency loading in Pyodide environment."""
        script_content = "import numpy as np\nnp.array([1,2,3]).sum()"

        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                new_callable=AsyncMock,
            ) as mock_eval_code_async,
            patch(
                "registry.tracecat_registry.core.python.micropip.install",
                new_callable=AsyncMock,
            ) as mock_micropip_install,
        ):
            mock_eval_code_async.return_value = 6  # sum of [1,2,3]

            result = await run_python(
                script=script_content, dependencies=["numpy", "pandas"]
            )

            assert result == 6
            mock_micropip_install.assert_any_call("numpy")
            mock_micropip_install.assert_any_call("pandas")
            assert mock_micropip_install.call_count == 2

    @pytest.mark.asyncio
    async def test_run_python_dependency_install_fail_pyodide(self, mock_logger):
        """Test handling of dependency installation failure in Pyodide."""
        script_content = "import non_existent_pkg\nnon_existent_pkg.do()"

        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                new_callable=AsyncMock,
            ) as mock_eval_code_async,
            patch(
                "registry.tracecat_registry.core.python.micropip.install",
                new_callable=AsyncMock,
            ) as mock_micropip_install,
        ):
            mock_micropip_install.side_effect = Exception("Failed to install")
            # Script will then fail on import non_existent_pkg
            mock_eval_code_async.side_effect = ImportError(
                "No module named non_existent_pkg"
            )

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(
                    script=script_content, dependencies=["non_existent_pkg"]
                )

            mock_logger.warning.assert_any_call(
                "Failed to install dependency 'non_existent_pkg': Failed to install"
            )
            assert "No module named non_existent_pkg" in str(exc_info.value)

    # --- Tests for _run_python_script_subprocess (fallback mechanism) ---
    @pytest.mark.asyncio
    async def test_subprocess_fallback_basic_execution(self, mock_logger):
        """Test basic script execution via Node.js subprocess fallback."""
        script = "print('Hello from subprocess')\nmy_val = 42\nmy_val"
        inputs_data = {"x": 10}
        expected_node_stdout = json.dumps(
            {
                "success": True,
                "result": 42,
                "stdout": "Hello from subprocess\n",
                "stderr": "",
                "error": None,
            }
        )

        with patch("asyncio.create_subprocess_exec") as mock_create_subprocess:
            # Mock process communication
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (
                expected_node_stdout.encode(),
                b"",
            )
            mock_process.returncode = 0
            mock_create_subprocess.return_value = mock_process

            # Call the internal subprocess runner directly for this test unit
            await _run_python_script_subprocess(script, inputs_data, None, 10)

            # Now test run_python which uses the above
            # Need to path eval_code_async to make run_python choose the fallback
        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                side_effect=ImportError,
            ),
            patch(
                "asyncio.create_subprocess_exec"
            ) as mock_create_subprocess_for_run_python,
        ):
            mock_process_for_run_python = AsyncMock()
            mock_process_for_run_python.communicate.return_value = (
                expected_node_stdout.encode(),
                b"",
            )
            mock_process_for_run_python.returncode = 0
            mock_create_subprocess_for_run_python.return_value = (
                mock_process_for_run_python
            )

            result = await run_python(script=script, inputs=inputs_data)

            assert result == 42
            mock_logger.info.assert_any_call("Script stdout:\nHello from subprocess")
            # Check that tempfile.NamedTemporaryFile was used to write node script
            # This is harder to check directly without more intrusive mocks or inspecting fs
            # We trust the Node.js script content is formed correctly if subprocess is called with 'node' and a path.
            call_args = mock_create_subprocess_for_run_python.call_args[0]
            assert call_args[0] == "node"
            assert call_args[1].endswith(".js")

    @pytest.mark.asyncio
    async def test_subprocess_fallback_script_error(self, mock_logger):
        """Test script error reporting from Node.js subprocess fallback."""
        script = "raise Exception('Subprocess Test Error')"
        expected_node_stdout = json.dumps(
            {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": "Exception: Subprocess Test Error\n",
                "error": "Exception: Subprocess Test Error",
            }
        )

        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                side_effect=ImportError,
            ),
            patch("asyncio.create_subprocess_exec") as mock_create_subprocess,
        ):
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (expected_node_stdout.encode(), b"")
            mock_process.returncode = (
                0  # Node script itself ran, but Python inside it failed
            )
            mock_create_subprocess.return_value = mock_process

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(script=script, inputs=None)

            assert "Subprocess Test Error" in str(exc_info.value)
            mock_logger.error.assert_any_call(
                "Script stderr:\nException: Subprocess Test Error"
            )
            mock_logger.error.assert_any_call(
                "Script execution failed: Exception: Subprocess Test Error"
            )

    @pytest.mark.asyncio
    async def test_subprocess_fallback_node_process_error(self, mock_logger):
        """Test when the Node.js process itself fails for the fallback."""
        script = "print('hello')"

        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                side_effect=ImportError,
            ),
            patch("asyncio.create_subprocess_exec") as mock_create_subprocess,
        ):
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (
                b"",
                b"Node.js critical error",
            )  # Node stderr
            mock_process.returncode = 1  # Node process failed
            mock_create_subprocess.return_value = mock_process

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(script=script, inputs=None)

            assert "Node.js process error" in str(exc_info.value)
            assert "Node.js critical error" in str(exc_info.value)
            mock_logger.error.assert_any_call(
                "Script stderr:\nNode.js process exited with code 1. Stderr: Node.js critical error"
            )
            mock_logger.error.assert_any_call(
                "Script execution failed: Node.js process error. Stderr: Node.js critical error"
            )

    @pytest.mark.asyncio
    async def test_subprocess_fallback_timeout(self, mock_logger):
        """Test script timeout with the Node.js subprocess fallback."""
        script = "import time; time.sleep(5)"
        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                side_effect=ImportError,
            ),
            patch("asyncio.create_subprocess_exec") as mock_create_subprocess,
        ):
            mock_process = AsyncMock()
            # Simulate asyncio.TimeoutError during process.communicate()
            mock_process.communicate.side_effect = asyncio.TimeoutError
            mock_create_subprocess.return_value = mock_process

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(script=script, inputs=None, timeout_seconds=1)

            assert "timed out" in str(exc_info.value).lower()
            assert "subprocess" in str(exc_info.value).lower()
            # Logger might not get specific stderr if subprocess communication itself times out
            mock_logger.error.assert_any_call(
                "Script execution failed: Script execution (subprocess) timed out after 1 seconds"
            )

    @pytest.mark.asyncio
    async def test_subprocess_json_decode_error(self, mock_logger):
        """Test handling of invalid JSON output from the Node.js subprocess."""
        script = "print('hello')"
        invalid_json_stdout = "This is not JSON"

        with (
            patch(
                "registry.tracecat_registry.core.python.eval_code_async",
                side_effect=ImportError,
            ),
            patch("asyncio.create_subprocess_exec") as mock_create_subprocess,
        ):
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (
                invalid_json_stdout.encode(),
                b"",
            )
            mock_process.returncode = 0
            mock_create_subprocess.return_value = mock_process

            with pytest.raises(RuntimeError) as exc_info:
                await run_python(script=script, inputs=None)

            assert "Failed to decode JSON output" in str(exc_info.value)
            mock_logger.error.assert_any_call(
                "Script stderr:\n"
            )  # Stderr from node might be empty
            mock_logger.error.assert_any_call(
                f"Script execution failed: Failed to decode JSON output from Node.js: Expecting value: line 1 column 1 (char 0). stdout: {invalid_json_stdout}"
            )
