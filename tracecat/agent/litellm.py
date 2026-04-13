"""Launcher for the managed LiteLLM service.

Runs LiteLLM on the Tracecat image while preserving the config path shape that
LiteLLM expects for custom auth and callback module resolution.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse


def prepare_runtime_config() -> Path:
    """Create the runtime config path LiteLLM resolves hooks against."""
    source_config = Path(__file__).with_name("litellm_config.yaml")
    if not source_config.exists():
        raise FileNotFoundError(f"LiteLLM config not found: {source_config}")

    runtime_config = Path("/app/litellm_config.yaml")
    temp_symlink = runtime_config.with_suffix(f".yaml.{os.getpid()}.tmp")
    try:
        temp_symlink.symlink_to(source_config)
        temp_symlink.replace(runtime_config)
    except FileExistsError:
        pass
    finally:
        if temp_symlink.exists() or temp_symlink.is_symlink():
            temp_symlink.unlink()

    return runtime_config


def build_exec_env() -> dict[str, str]:
    """Build the environment for the managed LiteLLM process."""
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    app_paths = "/app:/app/packages/tracecat-registry:/app/packages/tracecat-ee"
    env["PYTHONPATH"] = f"{app_paths}:{pythonpath}" if pythonpath else app_paths
    return env


def get_bind_host() -> str:
    """Return the bind host for the managed LiteLLM process."""
    if base_url := os.environ.get("TRACECAT__LITELLM_BASE_URL"):
        parsed = urlparse(base_url)
        if parsed.hostname in {"0.0.0.0", "127.0.0.1", "localhost"}:
            return parsed.hostname
    return "0.0.0.0"


def main() -> None:
    """Launch LiteLLM with the Tracecat-managed gateway config."""
    runtime_config = prepare_runtime_config()
    cmd = [
        "litellm",
        "--host",
        get_bind_host(),
        "--port",
        os.environ.get("TRACECAT__LITELLM_PORT") or "4000",
        "--num_workers",
        os.environ.get("TRACECAT__LITELLM_NUM_WORKERS") or "2",
        "--config",
        str(runtime_config),
    ]
    os.execvpe(cmd[0], cmd, build_exec_env())


if __name__ == "__main__":
    main()
