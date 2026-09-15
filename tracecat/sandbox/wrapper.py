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
import dataclasses
import datetime
import decimal
import enum
import errno
import importlib
import inspect
import io
import json
import os
import resource
import sys
import traceback
import uuid
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

def to_json_safe(value):
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, set | frozenset):
        try:
            return sorted(value)
        except TypeError:
            return sorted(value, key=repr)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return dataclasses.asdict(value)
        except RecursionError as e:
            raise TypeError("Recursive dataclass values are not JSON-serializable") from e
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, enum.Enum):
        return to_json_safe(value.value)
    if isinstance(value, uuid.UUID | Path):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="replace")
    return repr(value)

def _enforce_nproc_limit() -> None:
    """Cap the jail's process count via RLIMIT_NPROC.

    nsjail cannot enforce rlimit_nproc when it creates a user namespace
    (clone_newuser), so the host injects the configured limit via
    TRACECAT__SANDBOX_RLIMIT_NPROC and this trusted entrypoint applies it
    before any untrusted code runs. Lowering a hard rlimit is permitted for
    unprivileged processes, and the limit is enforced per real UID, which
    covers every process in the jail.

    Fails closed: if a cap was injected but cannot be enforced, refuse to
    run untrusted code rather than continuing uncapped. A missing cap
    (direct/dev mode) is not an enforcement failure.
    """
    raw = os.environ.get("TRACECAT__SANDBOX_RLIMIT_NPROC")
    if not raw:
        return
    try:
        limit = int(raw)
        if limit <= 0:
            raise ValueError(f"non-positive cap: {limit}")
        _soft, current_hard = resource.getrlimit(resource.RLIMIT_NPROC)
        finite = current_hard != resource.RLIM_INFINITY
        # Already capped when the inherited hard cap is at or below the
        # requested limit (including 0 = no child processes), or when a
        # stricter finite soft limit is in force: raising either would only
        # relax enforcement.
        already_capped = (
            0 <= current_hard <= limit
            if finite
            else 0 < _soft <= limit
        )
        if not already_capped:
            resource.setrlimit(resource.RLIMIT_NPROC, (limit, limit))
    except (ValueError, OSError, OverflowError) as exc:
        # Values are host-injected; a malformed value or an unenforceable
        # rlimit means the process cap is not in place. Exit instead of
        # running untrusted code without the enforced cap.
        raise SystemExit(
            f"_enforce_nproc_limit: could not enforce RLIMIT_NPROC={raw!r}: "
            f"{type(exc).__name__}"
        ) from exc


def _resource_limit_message(error):
    """Recognize Python allocator and syscall resource failures by type/errno."""
    if isinstance(error, MemoryError):
        return "Script exceeded the sandbox memory limit"
    if isinstance(error, OSError):
        if error.errno == errno.ENOMEM:
            return "Script exceeded the sandbox memory limit"
        if error.errno == errno.EFBIG:
            return "Script exceeded the sandbox file size limit"
    return None


def _release_exception_chain(error):
    """Drop the tracebacks, and the chain itself, of an exception and its causes.

    A traceback keeps its frames alive, and a frame's locals can be the very
    object that exhausted memory, so the fallback envelope would allocate
    against a cap that is still fully consumed.
    """
    seen = set()
    pending = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.__traceback__ = None
        # Both links, not the first one that happens to be set: an exception
        # can carry a distinct cause and context, and either frame can be the
        # one holding what exhausted the cap.
        for following in (current.__cause__, current.__context__):
            if following is not None:
                pending.append(following)
        current.__cause__ = None
        current.__context__ = None


def _execute_script(inputs):
    """Keep workload locals in a frame that can unwind before recovery."""
    script_path = Path("/work/script.py")
    script_code = script_path.read_text()
    _init_tracecat_context()

    script_globals = {"__name__": "__main__", "__file__": str(script_path)}
    exec(script_code, script_globals)
    main_func = script_globals.get("main")
    if main_func is None:
        # Only user-defined functions qualify, not imported callable objects.
        for name, obj in script_globals.items():
            if inspect.isfunction(obj) and not name.startswith("_"):
                main_func = obj
                break
    if main_func is None:
        raise ValueError("No callable function found in script")

    call = main_func(**inputs) if inputs else main_func()
    return _resolve_output(call)


def _capture_result():
    """Read inputs, execute the script, and capture ordinary workload errors."""
    inputs_path = Path("/work/inputs.json")
    inputs = json.loads(inputs_path.read_text()) if inputs_path.exists() else {}
    result = {
        "success": False,
        "output": None,
        "error": None,
        "traceback": None,
        "stdout": "",
        "stderr": "",
    }
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            result["output"] = _execute_script(inputs)
            result["success"] = True
        except Exception as exc:
            if _resource_limit_message(exc) is not None:
                # The outer boundary must unwind workload frames and release
                # their allocations before building the resource envelope.
                raise
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["traceback"] = traceback.format_exc()

        # Extraction can allocate another copy of the capture buffers. Keep
        # it inside the outer recovery boundary, but always restore streams.
        result["stdout"] = sys.stdout.getvalue()
        result["stderr"] = sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return result


def _write_result():
    """Write normal results, including the existing non-JSON output fallback."""
    result = _capture_result()
    try:
        encoded = json.dumps(result, default=to_json_safe)
    except (TypeError, ValueError, RecursionError) as exc:
        result["error"] = f"Output not JSON-serializable: {type(exc).__name__}: {exc}"
        result["success"] = False
        result["output"] = repr(result["output"])
        encoded = json.dumps(result)
    Path("/work/result.json").write_text(encoded)
    return result["success"]


def main():
    """Recover resource failures across input, execution, capture, and output."""
    _enforce_nproc_limit()
    try:
        success = _write_result()
    except (MemoryError, OSError) as exc:
        message = _resource_limit_message(exc)
        if message is None:
            raise
        # All workload and serialization frames have unwound, and streams
        # are restored. Drop their tracebacks before allocating the fixed
        # envelope. Opening the result file anew also truncates a partial
        # write left by EFBIG.
        _release_exception_chain(exc)
        Path("/work/result.json").write_text(
            json.dumps(
                {
                    "success": False,
                    "output": None,
                    "error": message,
                    "traceback": None,
                    "stdout": "",
                    "stderr": "",
                    "error_code": "resource_limit_exceeded",
                }
            )
        )
        success = False
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
'''

# Install script for package installation phase
# SECURITY: Dependencies are read from a JSON file to prevent code injection
# via malicious package names. Never interpolate user input into this script.
INSTALL_SCRIPT = """
import json
import os
import resource
import subprocess
import sys
from pathlib import Path


def _enforce_nproc_limit() -> None:
    # Cap the jail's process count via RLIMIT_NPROC before uv runs.
    #
    # nsjail cannot enforce rlimit_nproc when it creates a user namespace
    # (clone_newuser), so the host injects the configured limit via
    # TRACECAT__SANDBOX_RLIMIT_NPROC and this trusted entrypoint applies it
    # before any untrusted build backend runs. uv inherits the rlimit and
    # passes it to build-backend child processes, containing fork bombs from
    # malicious or broken source packages to this install's process cap.
    raw = os.environ.get("TRACECAT__SANDBOX_RLIMIT_NPROC")
    if not raw:
        return
    try:
        limit = int(raw)
        if limit <= 0:
            raise ValueError(f"non-positive cap: {limit}")
        _soft, current_hard = resource.getrlimit(resource.RLIMIT_NPROC)
        finite = current_hard != resource.RLIM_INFINITY
        # Already capped when the inherited hard cap is at or below the
        # requested limit (including 0 = no child processes), or when a
        # stricter finite soft limit is in force: raising either would only
        # relax enforcement.
        already_capped = (
            0 <= current_hard <= limit
            if finite
            else 0 < _soft <= limit
        )
        if not already_capped:
            resource.setrlimit(resource.RLIMIT_NPROC, (limit, limit))
    except (ValueError, OSError, OverflowError) as exc:
        # Values are host-injected; a malformed value or an unenforceable
        # rlimit means the process cap is not in place. Exit instead of
        # running untrusted code without the enforced cap.
        raise SystemExit(
            f"_enforce_nproc_limit: could not enforce RLIMIT_NPROC={raw!r}: "
            f"{type(exc).__name__}"
        ) from exc


_enforce_nproc_limit()

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
