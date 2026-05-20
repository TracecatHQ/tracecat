"""Wrapper script for executing user Python code in the sandbox.

The wrapper script is executed inside the nsjail sandbox and handles:
1. Reading inputs from a JSON file
2. Executing the user's script
3. Finding and calling the main function
4. Writing results back to a JSON file
"""

# The wrapper script content is embedded as a string constant
# to be written to the job directory before execution
WRAPPER_SCRIPT = '''
import asyncio
import importlib
import inspect
import json
import os
import sys
import traceback
from pathlib import Path

def _install_action_gateway_sdk_transport():
    socket_path = os.environ.get("TRACECAT__ACTION_GATEWAY_SOCKET")
    if not socket_path:
        return

    try:
        import httpx

        sdk_client = importlib.import_module("tracecat_registry.sdk.client")
    except ImportError:
        return

    tracecat_client_cls = getattr(sdk_client, "TracecatClient", None)
    if tracecat_client_cls is None:
        return

    if hasattr(tracecat_client_cls, "_request_url_and_transport"):
        return

    if getattr(tracecat_client_cls, "_tracecat_action_gateway_transport", False):
        return

    async def request(
        self,
        method,
        path,
        *,
        params=None,
        json=None,
        headers=None,
    ):
        request_headers = self._get_headers()
        if headers:
            request_headers.update(headers)

        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket_path),
            timeout=getattr(self, "_timeout", 120.0),
        ) as client:
            response = await client.request(
                method,
                f"http://tracecat-action-gateway/internal{path}",
                params=params,
                json=json,
                headers=request_headers,
            )

        if not response.is_success:
            self._handle_error_response(response)

        if not response.content:
            return None

        return response.json()

    tracecat_client_cls.request = request
    tracecat_client_cls._tracecat_action_gateway_transport = True

def _init_tracecat_context():
    _install_action_gateway_sdk_transport()
    try:
        from tracecat_registry.context import init_context_from_env
    except ImportError:
        return
    try:
        init_context_from_env()
    except ValueError:
        return

def _resolve_output(value):
    if not inspect.isawaitable(value):
        return value

    async def await_value():
        return await value

    return asyncio.run(await_value())

def main():
    """Execute user script and capture results."""
    # Read inputs from file
    inputs_path = Path("/work/inputs.json")
    if inputs_path.exists():
        inputs = json.loads(inputs_path.read_text())
    else:
        inputs = {}

    result = {
        "success": False,
        "output": None,
        "error": None,
        "traceback": None,
        "stdout": "",
        "stderr": "",
    }

    # Capture stdout/stderr
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # Read and execute the user script
        script_path = Path("/work/script.py")
        script_code = script_path.read_text()

        _init_tracecat_context()

        script_globals = {"__name__": "__main__", "__file__": str(script_path)}
        exec(script_code, script_globals)

        # Find the callable function
        main_func = script_globals.get("main")
        if main_func is None:
            # Look for the first non-private user-defined function.
            # Use inspect.isfunction to match only functions created with 'def',
            # not imported classes or other callables.
            for name, obj in script_globals.items():
                if inspect.isfunction(obj) and not name.startswith("_"):
                    main_func = obj
                    break

        if main_func is None:
            raise ValueError("No callable function found in script")

        # Call the function with inputs
        if inputs:
            call = main_func(**inputs)
        else:
            call = main_func()
        output = _resolve_output(call)

        result["success"] = True
        result["output"] = output

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()

    finally:
        # Capture stdout/stderr
        result["stdout"] = sys.stdout.getvalue()
        result["stderr"] = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Write result to file
    result_path = Path("/work/result.json")
    try:
        result_path.write_text(json.dumps(result))
    except (TypeError, ValueError) as e:
        # Handle non-JSON-serializable outputs (datetime, bytes, custom classes, etc.)
        # Convert output to string representation so we don't lose the result
        result["output"] = repr(result["output"])
        result["error"] = f"Output not JSON-serializable: {type(e).__name__}: {e}"
        result["success"] = False
        result_path.write_text(json.dumps(result))

    # Exit with appropriate code
    sys.exit(0 if result["success"] else 1)

if __name__ == "__main__":
    main()
'''

# Install script for package installation phase
# SECURITY: Dependencies are read from a JSON file to prevent code injection
# via malicious package names. Never interpolate user input into this script.
INSTALL_SCRIPT = """
import json
import os
import subprocess
import sys
from pathlib import Path

# Read dependencies from secure JSON file (written with 0o600 permissions)
deps_path = Path("/work/dependencies.json")
if not deps_path.exists():
    print("No dependencies.json found", file=sys.stderr)
    sys.exit(1)

deps = json.loads(deps_path.read_text())
if not isinstance(deps, list):
    print("dependencies.json must contain a list", file=sys.stderr)
    sys.exit(1)

if not deps:
    print("No dependencies to install")
    sys.exit(0)

# Build uv pip install command with PyPI index configuration
cmd = ["uv", "pip", "install", "--target", "/cache/site-packages", "--python", sys.executable]

# Add index URL if configured (supports private PyPI mirrors)
index_url = os.environ.get("UV_INDEX_URL")
if index_url:
    cmd.extend(["--index-url", index_url])

# Add extra index URLs if configured
extra_index_urls = os.environ.get("UV_EXTRA_INDEX_URL")
if extra_index_urls:
    for url in extra_index_urls.split(","):
        url = url.strip()
        if url:
            cmd.extend(["--extra-index-url", url])

cmd.extend(deps)

result = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print(result.stderr, file=sys.stderr)
    sys.exit(result.returncode)
print("Packages installed successfully")
"""
