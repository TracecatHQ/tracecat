"""Python executor for environments without nsjail.

This executor uses process-level isolation and runs user scripts in a dedicated
PID namespace when available. It is intended for environments where nsjail
cannot run (for example, without SYS_ADMIN).
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any

from tracecat.config import (
    TRACECAT__SANDBOX_CACHE_DIR,
    TRACECAT__SANDBOX_DEFAULT_TIMEOUT,
    TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS,
    TRACECAT__SANDBOX_PYPI_INDEX_URL,
)
from tracecat.logger import logger
from tracecat.sandbox.exceptions import (
    PackageInstallError,
    SandboxExecutionError,
    SandboxTimeoutError,
)
from tracecat.sandbox.types import SandboxResult

SAFE_WRAPPER_SCRIPT = '''
import inspect
import json
import sys
import traceback
from pathlib import Path

def main():
    """Execute user script and capture results."""
    work_dir = "{work_dir}"

    # Read inputs from file
    inputs_path = Path(work_dir) / "inputs.json"
    if inputs_path.exists():
        inputs = json.loads(inputs_path.read_text())
    else:
        inputs = {{}}

    result = {{
        "success": False,
        "output": None,
        "error": None,
        "traceback": None,
        "stdout": "",
        "stderr": "",
    }}

    # Capture stdout/stderr
    import io
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        # Read and execute the user script
        script_path = Path(work_dir) / "script.py"
        script_code = script_path.read_text()
        script_globals = {{"__name__": "__main__", "__file__": str(script_path)}}
        exec(script_code, script_globals)

        # Find the callable function
        main_func = script_globals.get("main")
        if main_func is None:
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
        result["error"] = f"{{type(e).__name__}}: {{e}}"
        result["traceback"] = traceback.format_exc()

    finally:
        result["stdout"] = sys.stdout.getvalue()
        result["stderr"] = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    # Write result to file
    result_path = Path(work_dir) / "result.json"
    try:
        result_path.write_text(json.dumps(result))
    except (TypeError, ValueError):
        # Output not JSON-serializable, convert to repr
        result["output"] = repr(result["output"])
        try:
            result_path.write_text(json.dumps(result))
        except Exception as e:
            result["output"] = None
            result["error"] = f"Output not JSON-serializable: {{type(e).__name__}}: {{e}}"
            result["success"] = False
            result_path.write_text(json.dumps(result))

    sys.exit(0 if result["success"] else 1)

if __name__ == "__main__":
    main()
'''


def _extract_dependency_base_name(dependency: str) -> str:
    """Extract base package name from dependency spec (without version/extras)."""
    name = dependency.strip()
    if ";" in name:
        name = name.split(";", 1)[0].strip()
    if "@" in name:
        name = name.split("@", 1)[0].strip()
    for sep in ("==", ">=", "<=", ">", "<", "~=", "!=", "["):
        if sep in name:
            name = name.split(sep, 1)[0].strip()
            break
    return name.strip()


def _normalize_distribution_name(name: str) -> str:
    """Normalize a distribution name using PEP 503 rules."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _extract_package_name(dependency: str) -> str:
    """Extract base package name from dependency spec."""
    base_name = _extract_dependency_base_name(dependency)
    return base_name.replace("-", "_")


class UnsafePidExecutor:
    """Executor for Python scripts without nsjail, using subprocess isolation."""

    def __init__(
        self,
        cache_dir: str = TRACECAT__SANDBOX_CACHE_DIR,
    ):
        self.cache_dir = Path(cache_dir)
        self.package_cache = self.cache_dir / "unsafe-pid-packages"
        self.uv_cache = self.cache_dir / "uv-cache"
        self._pid_namespace_available: bool | None = None

        self.package_cache.mkdir(parents=True, exist_ok=True)
        self.uv_cache.mkdir(parents=True, exist_ok=True)

    def _compute_cache_key(
        self,
        dependencies: list[str],
        workspace_id: str | None = None,
    ) -> str:
        normalized = sorted(dep.lower().strip() for dep in dependencies)
        if workspace_id:
            hash_input = f"{workspace_id}\n" + "\n".join(normalized)
        else:
            hash_input = "\n".join(normalized)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def _iter_site_packages_paths(self, venv_path: Path) -> list[Path]:
        candidates: list[Path] = []
        for lib_dir in ("lib", "lib64"):
            lib_root = venv_path / lib_dir
            if not lib_root.exists():
                continue
            for py_dir in sorted(lib_root.glob("python*")):
                for subdir in ("site-packages", "dist-packages"):
                    site_packages = py_dir / subdir
                    if site_packages.exists():
                        candidates.append(site_packages)

        windows_site_packages = venv_path / "Lib" / "site-packages"
        if windows_site_packages.exists():
            candidates.append(windows_site_packages)

        return candidates

    def _get_dependency_import_names(
        self,
        venv_path: Path,
        dependencies: list[str],
    ) -> set[str]:
        if not dependencies:
            return set()

        normalized_deps = {
            _normalize_distribution_name(_extract_dependency_base_name(dep))
            for dep in dependencies
        }
        normalized_deps.discard("")
        if not normalized_deps:
            return set()

        import_names: set[str] = set()
        for site_packages in self._iter_site_packages_paths(venv_path):
            try:
                distributions = metadata.distributions(path=[str(site_packages)])
            except Exception as exc:
                logger.debug(
                    "Failed to read dependency metadata",
                    site_packages=str(site_packages),
                    error=str(exc),
                )
                continue

            for dist in distributions:
                dist_name = dist.metadata.get("Name")
                if not dist_name:
                    continue
                if _normalize_distribution_name(dist_name) not in normalized_deps:
                    continue

                top_level = dist.read_text("top_level.txt")
                if top_level:
                    for line in top_level.splitlines():
                        module_name = line.strip()
                        if module_name:
                            import_names.add(module_name)
                else:
                    import_names.add(_extract_package_name(dist_name))

        return import_names

    def _get_allowed_modules(
        self,
        dependencies: list[str],
        venv_path: Path | None = None,
    ) -> set[str]:
        allowed: set[str] = set()
        for dep in dependencies:
            base_name = _extract_dependency_base_name(dep)
            if not base_name:
                continue
            allowed.add(base_name.replace("-", "_"))

        if venv_path is not None:
            allowed.update(self._get_dependency_import_names(venv_path, dependencies))

        return allowed

    def _is_pid_namespace_available(self) -> bool:
        if self._pid_namespace_available is not None:
            return self._pid_namespace_available

        if shutil.which("unshare") is None:
            self._pid_namespace_available = False
            return False

        try:
            probe = subprocess.run(
                ["unshare", "--pid", "--fork", "--kill-child", "true"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            self._pid_namespace_available = probe.returncode == 0
        except Exception:
            self._pid_namespace_available = False
        return self._pid_namespace_available

    def _build_execution_cmd(self, python_path: str, wrapper_path: Path) -> list[str]:
        base_cmd = [python_path, str(wrapper_path)]
        if self._is_pid_namespace_available():
            return ["unshare", "--pid", "--fork", "--kill-child", *base_cmd]

        logger.warning(
            "PID namespace isolation unavailable; running script without PID isolation"
        )
        return base_cmd

    async def _create_venv(self, venv_path: Path) -> None:
        create_cmd = ["uv", "venv", str(venv_path), "--python", "3.12"]
        process = await asyncio.create_subprocess_exec(
            *create_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "UV_CACHE_DIR": str(self.uv_cache),
            },
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError as e:
            process.kill()
            await process.wait()
            raise PackageInstallError("Virtual environment creation timed out") from e
        if process.returncode != 0:
            raise PackageInstallError(
                f"Failed to create virtual environment: {stderr.decode()}"
            )

    async def _install_packages(
        self,
        venv_path: Path,
        dependencies: list[str],
        timeout_seconds: int = 300,
    ) -> None:
        pip_cmd = [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_path / "bin" / "python"),
        ]

        if TRACECAT__SANDBOX_PYPI_INDEX_URL:
            pip_cmd.extend(["--index-url", TRACECAT__SANDBOX_PYPI_INDEX_URL])
        for extra_url in TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS:
            pip_cmd.extend(["--extra-index-url", extra_url])
        pip_cmd.extend(dependencies)

        process = await asyncio.create_subprocess_exec(
            *pip_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "UV_CACHE_DIR": str(self.uv_cache),
            },
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as e:
            process.kill()
            await process.wait()
            raise PackageInstallError(
                f"Package installation timed out after {timeout_seconds}s"
            ) from e

        if process.returncode != 0:
            raise PackageInstallError(f"Failed to install packages: {stderr.decode()}")

    async def execute(
        self,
        script: str,
        inputs: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        timeout_seconds: int | None = None,
        allow_network: bool = False,
        env_vars: dict[str, str] | None = None,
        workspace_id: str | None = None,
    ) -> SandboxResult:
        if timeout_seconds is None:
            timeout_seconds = TRACECAT__SANDBOX_DEFAULT_TIMEOUT

        start_time = time.time()
        if not allow_network:
            logger.info(
                "allow_network is not enforced without nsjail; configure nsjail for network isolation"
            )

        work_dir = Path(tempfile.mkdtemp(prefix="unsafe-pid-sandbox-"))

        try:
            python_path = shutil.which("python3") or "python3"
            if dependencies:
                cache_key = self._compute_cache_key(dependencies, workspace_id)
                cached_venv = self.package_cache / cache_key
                if not (
                    cached_venv.exists() and (cached_venv / "bin" / "python").exists()
                ):
                    temp_venv = self.package_cache / f"{cache_key}.{os.getpid()}.tmp"
                    try:
                        if temp_venv.exists():
                            shutil.rmtree(temp_venv, ignore_errors=True)
                        await self._create_venv(temp_venv)
                        await self._install_packages(
                            temp_venv,
                            dependencies,
                            timeout_seconds=timeout_seconds,
                        )
                        try:
                            os.rename(temp_venv, cached_venv)
                        except OSError:
                            logger.debug(
                                "Venv cache race: using existing venv",
                                cache_key=cache_key,
                            )
                    finally:
                        if temp_venv.exists():
                            shutil.rmtree(temp_venv, ignore_errors=True)

                python_path = str(cached_venv / "bin" / "python")

            (work_dir / "script.py").write_text(script)
            (work_dir / "inputs.json").write_text(json.dumps(inputs or {}))
            wrapper_path = work_dir / "wrapper.py"
            wrapper_path.write_text(SAFE_WRAPPER_SCRIPT.format(work_dir=str(work_dir)))

            exec_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
            if env_vars:
                exec_env.update(env_vars)

            cmd = self._build_execution_cmd(python_path, wrapper_path)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env=exec_env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
            except TimeoutError as e:
                process.kill()
                await process.wait()
                raise SandboxTimeoutError(
                    f"Script execution timed out after {timeout_seconds}s"
                ) from e

            execution_time_ms = (time.time() - start_time) * 1000
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            result_path = work_dir / "result.json"
            if result_path.exists():
                try:
                    result_data = json.loads(result_path.read_text())
                    return SandboxResult(
                        success=result_data.get("success", False),
                        output=result_data.get("output"),
                        stdout=result_data.get("stdout", stdout),
                        stderr=result_data.get("stderr", stderr),
                        error=result_data.get("error"),
                        exit_code=process.returncode,
                        execution_time_ms=execution_time_ms,
                    )
                except json.JSONDecodeError:
                    logger.warning("Failed to parse result.json")

            return SandboxResult(
                success=False,
                error=f"Execution failed: {stderr[:500] if stderr else 'Unknown error'}",
                stdout=stdout,
                stderr=stderr[:500] if stderr else "",
                exit_code=process.returncode,
                execution_time_ms=execution_time_ms,
            )

        except (SandboxTimeoutError, PackageInstallError):
            raise
        except Exception as e:
            logger.error(
                "Unexpected error in unsafe PID executor",
                error_type=type(e).__name__,
            )
            raise SandboxExecutionError(
                f"Unexpected error: {type(e).__name__}: {e}"
            ) from e
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
