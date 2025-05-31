from typing import Annotated, Any, TypedDict
from tracecat.logger import logger
from typing_extensions import Doc
import json

from tracecat_registry import registry


class PythonScriptOutput(TypedDict):
    """Internal representation of script execution output."""

    result: Any
    stdout: str
    stderr: str
    success: bool
    error: str | None


@registry.register(
    namespace="core.script",
    description="Execute a Python script in a sandboxed WebAssembly environment, similar to AWS Lambda.",
    default_title="Run Python Script",
    display_group="Code Execution",
)
async def run_python(
    script: Annotated[
        str,
        Doc(
            "Python script to execute. The script runs in a sandboxed WebAssembly environment. "
            "Input data is available via global variables from the 'event' parameter. "
            "The script's last evaluated expression is returned."
        ),
    ],
    event: Annotated[
        dict[str, Any] | None,
        Doc(
            "Input data for the script, like an AWS Lambda event. "
            "Each key-value pair becomes a global variable in the script."
        ),
    ] = None,
    packages: Annotated[
        list[str] | None,
        Doc(
            "Optional list of Python packages to load (e.g., ['numpy', 'pandas']). "
            "Only packages available in Pyodide are supported."
        ),
    ] = None,
    timeout_seconds: Annotated[
        int,
        Doc("Maximum execution time in seconds. Default is 30 seconds."),
    ] = 30,
) -> Any:
    """
    Executes a Python script in a sandboxed WebAssembly environment using Pyodide,
    following a model similar to AWS Lambda functions.

    The input 'event' dictionary's items are injected as global variables into the script.
    The script's standard output and standard error are logged.
    The value of the last evaluated expression in the script is returned.

    If the script encounters an error or times out, a RuntimeError is raised.

    Example:
        >>> # Script content:
        >>> # print(f"Processing item: {item_name}")
        >>> # result = quantity * price
        >>> # result  # This last expression is returned
        >>>
        >>> await run_python(
        ...     script="print(f\"Processing item: {item_name}\")\nnew_quantity = quantity * 2\nnew_quantity * price",
        ...     event={"item_name": "WidgetA", "quantity": 10, "price": 5.0}
        ... )
        # stdout will be logged: "Processing item: WidgetA"
        # Returns: 100.0 ( (10*2) * 5.0 )

    Args:
        script: The Python script content.
        event: A dictionary of input data, made available as global variables to the script.
        packages: A list of Pyodide-compatible packages to install before execution.
        timeout_seconds: Maximum allowed execution time for the script.

    Returns:
        The result of the last evaluated expression in the script.

    Raises:
        RuntimeError: If script execution fails, times out, or if the Pyodide environment
                      fails to initialize.
    """
    import asyncio
    import sys
    from io import StringIO

    # Determine if we are in a Pyodide environment or need to use subprocess fallback
    in_pyodide_env = False
    try:
        from pyodide import eval_code_async  # type: ignore

        in_pyodide_env = True
    except ImportError:
        # Will use _run_python_script_subprocess fallback
        pass

    internal_output: PythonScriptOutput

    if in_pyodide_env:
        stdout_capture = StringIO()
        stderr_capture = StringIO()

        # Prepare script globals from the event dictionary
        script_globals = {"__name__": "__main__"}
        if event:
            script_globals.update(event)

        if packages:
            import micropip  # type: ignore

            for package in packages:
                try:
                    await micropip.install(package)
                except Exception as e:
                    # Log package installation error and continue, or raise?
                    # For now, log and let script fail if import is missing
                    logger.warning(f"Failed to install package '{package}': {e}")

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture

        try:
            script_result = await asyncio.wait_for(
                eval_code_async(script, globals=script_globals),  # Use 'globals' kwarg
                timeout=timeout_seconds,
            )
            if hasattr(script_result, "to_py"):  # Convert JS proxy if needed
                script_result = script_result.to_py()

            internal_output = PythonScriptOutput(
                result=script_result,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                success=True,
                error=None,
            )
        except asyncio.TimeoutError:
            internal_output = PythonScriptOutput(
                result=None,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                success=False,
                error=f"Script execution timed out after {timeout_seconds} seconds",
            )
        except Exception as e:
            internal_output = PythonScriptOutput(
                result=None,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                success=False,
                error=str(e),
            )
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
    else:
        # Fallback to subprocess execution
        internal_output = await _run_python_script_subprocess(
            script, event, packages, timeout_seconds
        )

    # Log stdout and stderr
    if internal_output["stdout"]:
        logger.info(f"Script stdout:\n{internal_output['stdout'].strip()}")
    if internal_output["stderr"]:
        logger.error(f"Script stderr:\n{internal_output['stderr'].strip()}")

    # Handle results
    if internal_output["success"]:
        return internal_output["result"]
    else:
        error_message = internal_output["error"] or "Unknown script execution error"
        logger.error(f"Script execution failed: {error_message}")
        raise RuntimeError(f"Script execution failed: {error_message}")


async def _run_python_script_subprocess(
    script: str,
    event: dict[str, Any] | None,
    packages: list[str] | None,
    timeout_seconds: int,
) -> PythonScriptOutput:
    """
    Fallback implementation using subprocess to run Pyodide via Node.js.
    This is useful for server-side execution where browser APIs aren't available.
    """
    import tempfile
    import asyncio
    # json import is at the top level

    # Create a Node.js script that uses Pyodide
    # The event object is directly injected into the pyodide.globals
    # so Python script can access its keys as global variables.
    node_script_template = """
const {{ loadPyodide }} = require("pyodide");

async function main() {{
    const pyodide = await loadPyodide();

    // Load packages if requested
    const packages = {packages_json};
    if (packages && packages.length > 0) {{
        try {{
            await pyodide.loadPackage(packages);
        }} catch (pkgError) {{
            console.error(`Error loading packages: ${{pkgError.toString()}}`);
            // Continue, script might fail on import
        }}
    }}

    // Set up globals from the event object
    const eventObj = {event_json};
    if (eventObj) {{
        for (const [key, value] of Object.entries(eventObj)) {{
            pyodide.globals.set(key, value);
        }}
    }}

    let stdout_acc = "";
    let stderr_acc = "";
    pyodide.setStdout({{
        batched: (msg) => {{ stdout_acc += msg + "\\n"; }}
    }});
    pyodide.setStderr({{
        batched: (msg) => {{ stderr_acc += msg + "\\n"; }}
    }});

    let scriptResult = null;
    try {{
        scriptResult = await pyodide.runPythonAsync(`{escaped_script}`);
        if (typeof scriptResult?.toJs === 'function') {{
             scriptResult = scriptResult.toJs({{ dict_converter: Object.fromEntries }});
        }}
        console.log(JSON.stringify({{
            success: true,
            result: scriptResult,
            stdout: stdout_acc,
            stderr: stderr_acc,
            error: null
        }}));
    }} catch (error) {{
        console.log(JSON.stringify({{
            success: false,
            result: null,
            stdout: stdout_acc,
            stderr: stderr_acc,
            error: error.toString()
        }}));
    }}
}}

main().catch(err => {{
    // Ensure some JSON output even if main itself fails
    console.log(JSON.stringify({{
        success: false,
        result: null,
        stdout: "",
        stderr: `Node.js wrapper error: ${{err.toString()}}`,
        error: `Node.js wrapper error: ${{err.toString()}}`
    }}));
}});
"""

    escaped_script = (
        script.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    )

    node_script = node_script_template.format(
        packages_json=json.dumps(packages or []),
        event_json=json.dumps(event or {}),
        escaped_script=escaped_script,
    )
    temp_file_path = ""  # Ensure it's defined for finally block
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(node_script)
            temp_file_path = f.name

        process = await asyncio.create_subprocess_exec(
            "node",
            temp_file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds + 5
        )  # Add buffer for node startup

        stdout = stdout_bytes.decode().strip()
        stderr = stderr_bytes.decode().strip()

        if process.returncode != 0:
            return PythonScriptOutput(
                result=None,
                stdout="",  # stdout might contain partial JSON, stderr is more reliable
                stderr=f"Node.js process exited with code {process.returncode}. Stderr: {stderr}",
                success=False,
                error=f"Node.js process error. Stderr: {stderr}",
            )

        if stdout:  # stdout should contain the JSON from the Node script
            output_data = json.loads(stdout)
            # Ensure all fields are present, defaulting if necessary
            return PythonScriptOutput(
                result=output_data.get("result"),
                stdout=output_data.get("stdout", ""),
                stderr=output_data.get("stderr", ""),
                success=output_data.get("success", False),
                error=output_data.get("error"),
            )
        else:  # No JSON output from Node, implies a problem
            return PythonScriptOutput(
                result=None,
                stdout="",
                stderr=stderr or "No output from Pyodide execution via Node.js.",
                success=False,
                error=stderr or "No output from Pyodide Node.js script.",
            )

    except asyncio.TimeoutError:
        return PythonScriptOutput(
            result=None,
            stdout="",
            stderr="",
            success=False,
            error=f"Script execution (subprocess) timed out after {timeout_seconds} seconds",
        )
    except json.JSONDecodeError as jde:
        return PythonScriptOutput(
            result=None,
            stdout=stdout,  # Include what was received
            stderr=stderr,
            success=False,
            error=f"Failed to decode JSON output from Node.js: {jde}. stdout: {stdout}",
        )
    except Exception as e:
        return PythonScriptOutput(
            result=None,
            stdout="",
            stderr=str(e),
            success=False,
            error=f"Failed to execute script via subprocess: {str(e)}",
        )
    finally:
        if temp_file_path:
            try:
                import os

                os.unlink(temp_file_path)
            except (OSError, NameError):  # NameError if temp_file_path wasn't assigned
                pass
