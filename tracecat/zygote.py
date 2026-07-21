"""Development-only fork supervisor for standalone Tracecat instances.

The parent imports :mod:`tracecat.standalone` below to warm the full module
graph, then freezes tracked objects before forking. It must remain a zygote:
do not create an asyncio event loop, service client, database engine, or thread
on the parent path before ``os.fork()``.

The active Tracecat Loguru configuration writes synchronously to stderr and
does not start a writer thread. ``tracecat.logger.config`` contains optional
file-sink templates with ``enqueue=True``, but standalone does not install
those templates. If that changes, forking with the inherited writer state will
be unsafe and this entrypoint must be revisited.

Child configuration re-derivation explicitly reloads ``tracecat.config`` and
then ``tracecat.agent.common.config``. Modules imported into the zygote that
consume per-instance settings access those module objects lazily; the other
imported modules with direct environment reads contain shared process settings
or constants for paths inside per-job sandbox namespaces.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import NoReturn
from urllib.parse import urlsplit

# This import is intentionally module-level: it is the zygote warm-up step.
from tracecat import config, standalone
from tracecat.agent.common import config as agent_config
from tracecat.logger import logger

DEFAULT_INSTANCE_DIR = Path("/etc/tracecat/instances")
SHUTDOWN_TIMEOUT_SECONDS = 40.0
_WAIT_INTERVAL_SECONDS = 0.1
_ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class InstanceManifest:
    """One instance loaded from a manifest environment file."""

    name: str
    path: Path
    environment: dict[str, str]


@dataclass(frozen=True, slots=True)
class ChildProcess:
    """A live forked standalone child."""

    instance: InstanceManifest
    pid: int


class CliArgs(argparse.Namespace):
    """Typed command-line arguments."""

    dry_run: bool = False


def _parse_env_file(path: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")
        if separator != "=" or _ENV_KEY_PATTERN.fullmatch(key) is None:
            raise ValueError(f"{path}:{lineno}: expected KEY=VALUE")
        if key in environment:
            raise ValueError(f"{path}:{lineno}: duplicate key {key!r}")
        if key == "TRACECAT_INSTANCE" and value != path.stem:
            raise ValueError(
                f"{path}:{lineno}: TRACECAT_INSTANCE must match filename stem "
                f"{path.stem!r}"
            )
        environment[key] = value
    return environment


def _read_manifest(directory: Path) -> tuple[InstanceManifest, ...]:
    if not directory.is_dir():
        raise ValueError(f"Instance manifest directory does not exist: {directory}")

    paths = sorted(path for path in directory.glob("*.env") if path.is_file())
    if not paths:
        raise ValueError(f"No instance .env files found in {directory}")

    return tuple(
        InstanceManifest(
            name=path.stem,
            path=path,
            environment=_parse_env_file(path),
        )
        for path in paths
    )


def _apply_instance_environment(instance: InstanceManifest) -> None:
    os.environ.update(instance.environment)
    instance_name = instance.environment.get("TRACECAT_INSTANCE", instance.name)
    os.environ["TRACECAT_INSTANCE"] = instance_name

    def set_instance_default(key: str, value: str) -> None:
        if key not in instance.environment:
            os.environ[key] = value

    set_instance_default("TRACECAT__DB_NAME", instance_name)
    set_instance_default("TEMPORAL__CLUSTER_NAMESPACE", instance_name)
    instance_runtime_dir = Path("/run/tracecat") / instance_name
    set_instance_default(
        "TRACECAT__ACTION_GATEWAY_SOCKET",
        str(instance_runtime_dir / "action-gateway.sock"),
    )
    set_instance_default(
        "TRACECAT__AGENT_MCP_SOCKET_PATH",
        str(instance_runtime_dir / "mcp.sock"),
    )
    for suffix in ("attachments", "registry", "skills", "workflow", "agent"):
        set_instance_default(
            f"TRACECAT__BLOB_STORAGE_BUCKET_{suffix.upper()}",
            f"{instance_name}-tracecat-{suffix}",
        )

    port_value = instance.environment.get("PORT")
    if port_value is None:
        raise ValueError(f"{instance.path}: PORT is required")
    try:
        port = int(port_value)
    except ValueError as e:
        raise ValueError(f"{instance.path}: PORT must be an integer") from e
    if not 1 <= port <= 65535:
        raise ValueError(f"{instance.path}: PORT must be between 1 and 65535")

    internal_api_url = f"http://127.0.0.1:{port}"
    set_instance_default("TRACECAT__API_URL", internal_api_url)
    set_instance_default("TRACECAT__PUBLIC_API_URL", f"{internal_api_url}/api")
    set_instance_default("TRACECAT__PUBLIC_APP_URL", internal_api_url)
    set_instance_default(
        "TRACECAT__ALLOW_ORIGINS", os.environ["TRACECAT__PUBLIC_APP_URL"]
    )

    for required_key in ("TRACECAT__DB_URI", "REDIS_URL"):
        if required_key not in instance.environment:
            raise ValueError(f"{instance.path}: {required_key} is required")


def _rederive_child_config() -> None:
    """Re-execute config after applying the child's environment.

    ``importlib.reload`` preserves each module object, so consumers using
    module attribute access observe the newly derived attributes. Reload the
    application config first because the agent sandbox config is an independent
    direct environment reader.
    """
    importlib.reload(config)
    importlib.reload(agent_config)


def _db_host_and_database(uri: str) -> str:
    parsed = urlsplit(uri)
    hostname = parsed.hostname
    database = parsed.path.lstrip("/")
    if not hostname or not database:
        raise ValueError("TRACECAT__DB_URI must include a host and database")

    display_host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None:
        display_host = f"{display_host}:{parsed.port}"
    return f"{display_host}/{database}"


def _print_dry_run() -> None:
    payload = {
        "instance": os.environ["TRACECAT_INSTANCE"],
        "db_uri_host_and_db_only": _db_host_and_database(config.TRACECAT__DB_URI),
        "temporal_namespace": config.TEMPORAL__CLUSTER_NAMESPACE,
        "port": int(os.environ["PORT"]),
        "redis_url": config.REDIS_URL,
        "bucket_workflow": config.TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW,
        "action_gateway_socket": config.TRACECAT__ACTION_GATEWAY_SOCKET,
        "agent_mcp_socket": str(agent_config.TRACECAT__AGENT_MCP_SOCKET_PATH),
    }
    print(json.dumps(payload, sort_keys=True), flush=True)


def _run_child(instance: InstanceManifest, *, dry_run: bool) -> NoReturn:
    exit_code = 1
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        gc.enable()
        _apply_instance_environment(instance)
        _rederive_child_config()
        if dry_run:
            _print_dry_run()
            exit_code = 0
        else:
            exit_code = standalone.run()
    except BaseException:
        logger.exception(
            "Zygote child failed",
            instance=instance.name,
            pid=os.getpid(),
        )
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)


def _signal_children(children: dict[int, ChildProcess], sig: signal.Signals) -> None:
    for child in tuple(children.values()):
        try:
            os.kill(child.pid, sig)
        except ProcessLookupError:
            pass


def _supervise(instances: tuple[InstanceManifest, ...], *, dry_run: bool) -> int:
    children: dict[int, ChildProcess] = {}
    failed = False
    shutdown_requested = False
    shutdown_deadline: float | None = None
    sent_sigkill = False

    def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True

    previous_handlers = {
        sig: signal.signal(sig, request_shutdown)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }

    try:
        gc.freeze()
        for instance in instances:
            if shutdown_requested:
                failed = True
                break
            try:
                pid = os.fork()
            except OSError:
                logger.exception("Failed to fork zygote child", instance=instance.name)
                failed = True
                shutdown_requested = True
                break
            if pid == 0:
                _run_child(instance, dry_run=dry_run)
            children[pid] = ChildProcess(instance=instance, pid=pid)

        while children:
            if shutdown_requested and shutdown_deadline is None:
                logger.info(
                    "Forwarding SIGTERM to zygote children",
                    child_count=len(children),
                )
                _signal_children(children, signal.SIGTERM)
                shutdown_deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS

            if (
                shutdown_deadline is not None
                and not sent_sigkill
                and time.monotonic() >= shutdown_deadline
            ):
                logger.warning(
                    "Killing zygote children after shutdown timeout",
                    child_count=len(children),
                    timeout_seconds=SHUTDOWN_TIMEOUT_SECONDS,
                )
                _signal_children(children, signal.SIGKILL)
                sent_sigkill = True

            reaped_child = False
            for pid in tuple(children):
                waited_pid, status = os.waitpid(pid, os.WNOHANG)
                if waited_pid == 0:
                    continue
                reaped_child = True
                child = children.pop(waited_pid)
                exit_code = os.waitstatus_to_exitcode(status)
                logger.info(
                    "Zygote child exited",
                    instance=child.instance.name,
                    pid=waited_pid,
                    exit_code=exit_code,
                )
                if exit_code != 0:
                    failed = True

            if children and not reaped_child:
                time.sleep(_WAIT_INTERVAL_SECONDS)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    return 1 if failed else 0


def _parse_args(argv: list[str] | None) -> CliArgs:
    parser = argparse.ArgumentParser(
        description="Fork standalone Tracecat instances from one warmed zygote"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fork children, print derived configuration, and exit",
    )
    args = CliArgs()
    parser.parse_args(argv, namespace=args)
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    instance_directory = Path(
        os.environ.get("TRACECAT__ZYGOTE_INSTANCE_DIR", DEFAULT_INSTANCE_DIR)
    )
    try:
        instances = _read_manifest(instance_directory)
    except (OSError, UnicodeError, ValueError) as e:
        logger.error("Invalid zygote instance manifest", error=str(e))
        return 1
    return _supervise(instances, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
