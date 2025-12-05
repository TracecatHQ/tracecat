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
import inspect
import json
import sys
import traceback
from pathlib import Path

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

        script_globals = {"__name__": "__main__"}
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
            output = main_func(**inputs)
        else:
            output = main_func()

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
