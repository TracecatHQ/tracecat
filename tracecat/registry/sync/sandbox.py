"""NsJail-backed package installation and discovery for registry sync."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import stat
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import tracecat_registry
from pydantic import ValidationError

import tracecat
from tracecat import config
from tracecat.exceptions import RegistryError
from tracecat.git.types import GitUrl
from tracecat.git.utils import parse_git_url
from tracecat.registry.sync.artifact import (
    RegistryArtifactBuildError,
    RegistryArtifactBuildResult,
)
from tracecat.registry.sync.schemas import (
    RegistryCloneResult,
    SyncResultAdapter,
    SyncResultError,
    SyncResultSuccess,
)
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import ResourceLimits, SandboxBindMount, SandboxConfig
from tracecat.sandbox.wrapper import INSTALL_SCRIPT, WRAPPER_SCRIPT
from tracecat.ssh import add_ssh_key_to_agent, temporary_ssh_agent

_INSTALL_CACHE_KEY = "0" * 16
_KEYSCAN_TIMEOUT_SECONDS = 30
_GIT_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SSH_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$"
)
_SSH_USER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GIT_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_SQUASHFS_MEMORY_PATTERN = re.compile(r"^[1-9][0-9]*[KMG]$")

_KEYSCAN_SCRIPT = """
import json
import subprocess
from pathlib import Path

inputs = json.loads(Path("/work/inputs.json").read_text())
command = ["ssh-keyscan", "-H"]
if inputs["port"] is not None:
    command.extend(["-p", str(inputs["port"])])
command.append(inputs["host"])

result_payload = {"success": False, "output": None, "error": None}
try:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "ssh-keyscan exited unsuccessfully"
        raise RuntimeError(detail)
    result_payload["success"] = True
    result_payload["output"] = completed.stdout
except Exception as exc:
    result_payload["error"] = f"{type(exc).__name__}: {exc}"

Path("/work/result.json").write_text(json.dumps(result_payload))
raise SystemExit(0 if result_payload["success"] else 1)
"""

_CLONE_SCRIPT = """
import re
import subprocess
from pathlib import Path

_FULL_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _run(args, *, cwd=None):
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Git command failed: {args[1]}: {detail[:2000]}")
    return result.stdout.strip()


def main(clone_url, commit_sha=None):
    work = Path("/work")
    repo = work / "repo"
    template = work / "empty-template"
    template.mkdir(exist_ok=True)

    if commit_sha:
        _run(["git", "check-ref-format", "--branch", commit_sha])

    _run(
        [
            "git",
            "clone",
            "--no-checkout",
            "--depth",
            "1",
            f"--template={template}",
            "--",
            clone_url,
            str(repo),
        ]
    )

    if commit_sha:
        _run(
            ["git", "fetch", "--depth", "1", "--", "origin", commit_sha],
            cwd=repo,
        )
        target = "FETCH_HEAD"
    else:
        target = "HEAD"

    _run(["git", "checkout", "--detach", target], cwd=repo)
    resolved = _run(["git", "rev-parse", "HEAD^{commit}"], cwd=repo).lower()
    if not _FULL_OBJECT_ID.fullmatch(resolved):
        raise ValueError("Git returned an invalid resolved commit ID")
    return {"commit_sha": resolved}
"""

_PACKAGING_SCRIPT = """
import os
import stat
import subprocess
from pathlib import Path


def _validate_tree(root):
    for current_root, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        for name in [*directory_names, *file_names]:
            path = Path(current_root) / name
            mode = path.lstat().st_mode
            if not (
                stat.S_ISREG(mode)
                or stat.S_ISDIR(mode)
                or stat.S_ISLNK(mode)
            ):
                raise ValueError(f"Unsupported artifact entry type: {path}")


def main(processors, memory):
    source = Path("/input/site-packages")
    output = Path("/work/site-packages.squashfs")
    _validate_tree(source)
    output.unlink(missing_ok=True)
    result = subprocess.run(
        [
            "/usr/bin/mksquashfs",
            str(source),
            str(output),
            "-noappend",
            "-comp",
            "gzip",
            "-no-xattrs",
            "-all-root",
            "-processors",
            str(processors),
            "-mem",
            memory,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Failed to build SquashFS image: {detail[:2000]}")
    if not output.is_file() or output.is_symlink():
        raise RuntimeError("mksquashfs did not create a regular artifact file")
    return {"artifact_name": output.name}
"""

_DISCOVERY_SCRIPT = """
import asyncio
from uuid import UUID

from tracecat.registry.sync.entrypoint import load_and_serialize_installed_actions
from tracecat.registry.sync.schemas import SyncResultError


def main(
    origin,
    package_name,
    repository_id,
    commit_sha=None,
    validate=False,
    organization_id=None,
):
    try:
        result = asyncio.run(
            load_and_serialize_installed_actions(
                origin=origin,
                package_name=package_name,
                repository_id=UUID(repository_id),
                commit_sha=commit_sha,
                validate=validate,
                organization_id=(UUID(organization_id) if organization_id else None),
            )
        )
    except Exception as exc:
        return SyncResultError(error=str(exc)).model_dump(mode="json")
    return result.model_dump(mode="json")
"""


def _hash_file(path: Path) -> str:
    """Return the SHA-256 digest of a regular artifact file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _ValidatedGitSshTarget:
    """Validated connection data shared by keyscan and SSH-agent constraints."""

    host: str
    port: int | None
    destination: str


def _validate_git_ssh_target(git_url: GitUrl) -> _ValidatedGitSshTarget:
    """Return validated SSH keyscan and agent-destination components.

    OpenSSH constrains agent use to an SSH destination, not to a repository
    path. The provisioned registry key must therefore be a server-side,
    read-only deploy key for the requested repository.
    """
    host, separator, port = git_url.host.rpartition(":")
    if not separator:
        host = git_url.host
        port = ""
    if not _SSH_HOST_PATTERN.fullmatch(host):
        raise RegistryError("Registry sync Git host is invalid")
    if port and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise RegistryError("Registry sync Git port is invalid")
    if not _SSH_USER_PATTERN.fullmatch(git_url.user):
        raise RegistryError("Registry sync Git SSH user is invalid")
    if not _GIT_PATH_PATTERN.fullmatch(f"{git_url.org}/{git_url.repo}"):
        raise RegistryError("Registry sync Git repository path is invalid")
    if any(
        segment in {"", ".", ".."}
        for segment in [*git_url.org.split("/"), git_url.repo]
    ):
        raise RegistryError("Registry sync Git repository path is invalid")
    destination_host = f"[{host}]:{port}" if port else host
    return _ValidatedGitSshTarget(
        host=host,
        port=int(port) if port else None,
        destination=f"{git_url.user}@{destination_host}",
    )


def _host_site_packages_paths() -> list[Path]:
    """Return distinct host dependency roots needed by trusted sync code."""
    paths: list[Path] = []
    seen: set[Path] = set()
    for name in ("purelib", "platlib"):
        value = sysconfig.get_path(name)
        if value is None:
            continue
        path = Path(value).resolve()
        if path.is_dir() and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _trusted_runtime_package_paths() -> list[Path]:
    """Return trusted source packages needed by discovery in editable installs."""
    package_paths: list[Path] = []
    for module in (tracecat, tracecat_registry):
        if module.__file__ is None:
            raise RegistryArtifactBuildError(
                f"Cannot locate trusted runtime package {module.__name__}"
            )
        package_paths.append(Path(module.__file__).resolve().parent)
    return package_paths


class RegistrySyncSandbox:
    """Run untrusted registry installation and imports inside NsJail."""

    async def _scan_host_keys(
        self,
        *,
        target: _ValidatedGitSshTarget,
        work_dir: Path,
    ) -> Path:
        """Acquire hashed SSH host keys in a networked jail without credentials."""
        keyscan_dir = work_dir / "sandbox-keyscan"
        keyscan_dir.mkdir(parents=True, exist_ok=True)
        (keyscan_dir / "keyscan.py").write_text(_KEYSCAN_SCRIPT, encoding="utf-8")
        (keyscan_dir / "inputs.json").write_text(
            json.dumps({"host": target.host, "port": target.port}),
            encoding="utf-8",
        )

        try:
            result = await NsjailExecutor().execute(
                keyscan_dir,
                SandboxConfig(
                    network_enabled=True,
                    resources=ResourceLimits(
                        timeout_seconds=_KEYSCAN_TIMEOUT_SECONDS,
                        cpu_seconds=_KEYSCAN_TIMEOUT_SECONDS,
                        memory_mb=128,
                        max_file_size_mb=1,
                        max_processes=16,
                    ),
                ),
                script_name="keyscan.py",
            )
        except SandboxTimeoutError as exc:
            raise RegistryError(
                "Sandboxed SSH host key scan timed out after 30 seconds"
            ) from exc

        if not result.success:
            detail = result.error or result.stderr or "Unknown ssh-keyscan error"
            raise RegistryError(f"Sandboxed SSH host key scan failed: {detail[:2000]}")
        if not isinstance(result.output, str) or not result.output.strip():
            raise RegistryError("Sandboxed SSH host key scan returned no host keys")

        ssh_dir = work_dir / "registry-ssh"
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        ssh_dir.chmod(0o700)
        known_hosts_path = ssh_dir / "known_hosts"
        known_hosts_path.touch(mode=0o600, exist_ok=False)
        output = result.output
        known_hosts_path.write_text(
            output if output.endswith("\n") else f"{output}\n",
            encoding="utf-8",
        )
        known_hosts_path.chmod(0o600)
        return known_hosts_path

    async def clone_repository(
        self,
        *,
        git_url: str,
        commit_sha: str | None,
        ssh_key: str,
        work_dir: Path,
        timeout_seconds: int,
    ) -> tuple[Path, str]:
        """Clone one Git repository in a credential-scoped networked jail."""
        try:
            parsed_url = parse_git_url(git_url)
        except ValueError as exc:
            raise RegistryError("Invalid Git SSH URL for registry sync") from exc
        target = _validate_git_ssh_target(parsed_url)

        if commit_sha is not None and not _GIT_OBJECT_ID_PATTERN.fullmatch(commit_sha):
            raise RegistryError("Registry sync commit SHA is invalid")
        requested_commit = commit_sha or parsed_url.ref

        normalized_url = GitUrl(
            host=parsed_url.host,
            org=parsed_url.org,
            repo=parsed_url.repo,
            user=parsed_url.user,
        ).to_url()
        clone_url = normalized_url.replace("git+ssh://", "ssh://", 1)

        known_hosts_path = await self._scan_host_keys(
            target=target,
            work_dir=work_dir,
        )

        clone_root = work_dir / "sandbox-clone"
        job_dir = clone_root / "work"
        agent_dir = clone_root / "agent"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "script.py").write_text(_CLONE_SCRIPT, encoding="utf-8")
        (job_dir / "wrapper.py").write_text(WRAPPER_SCRIPT, encoding="utf-8")
        (job_dir / "inputs.json").write_text(
            json.dumps(
                {
                    "clone_url": clone_url,
                    "commit_sha": requested_commit,
                }
            ),
            encoding="utf-8",
        )

        async with temporary_ssh_agent(socket_dir=agent_dir) as ssh_env:
            await add_ssh_key_to_agent(
                ssh_key,
                ssh_env,
                lifetime_seconds=timeout_seconds + 30,
                destination=target.destination,
                known_hosts_path=known_hosts_path,
            )
            agent_socket = Path(ssh_env.ssh_auth_sock)
            sandbox_agent_dir = Path("/run/registry-agent")
            sandbox_agent_socket = sandbox_agent_dir / agent_socket.name
            sandbox_known_hosts = Path("/run/registry-ssh/known_hosts")
            ssh_command = " ".join(
                [
                    "ssh",
                    "-F /dev/null",
                    f"-o IdentityAgent={sandbox_agent_socket}",
                    "-o BatchMode=yes",
                    "-o ForwardAgent=no",
                    "-o StrictHostKeyChecking=yes",
                    f"-o UserKnownHostsFile={sandbox_known_hosts}",
                    "-o GlobalKnownHostsFile=/dev/null",
                ]
            )
            git_config = (
                ("core.hooksPath", "/dev/null"),
                ("init.templateDir", "/work/empty-template"),
                ("credential.helper", ""),
                ("protocol.file.allow", "never"),
                ("protocol.ext.allow", "never"),
            )
            env_vars = {
                "SSH_AUTH_SOCK": str(sandbox_agent_socket),
                "GIT_SSH_COMMAND": ssh_command,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_PROTOCOL_FROM_USER": "0",
                "XDG_CONFIG_HOME": "/tmp/xdg",
                "GIT_CONFIG_COUNT": str(len(git_config)),
            }
            for index, (key, value) in enumerate(git_config):
                env_vars[f"GIT_CONFIG_KEY_{index}"] = key
                env_vars[f"GIT_CONFIG_VALUE_{index}"] = value

            executor = NsjailExecutor()
            result = await executor.execute(
                job_dir,
                SandboxConfig(
                    network_enabled=True,
                    resources=ResourceLimits(
                        timeout_seconds=timeout_seconds,
                        cpu_seconds=timeout_seconds,
                        memory_mb=1024,
                        max_file_size_mb=512,
                    ),
                    env_vars=env_vars,
                    bind_mounts=[
                        SandboxBindMount(
                            source=agent_socket.parent,
                            destination=sandbox_agent_dir,
                        ),
                        SandboxBindMount(
                            source=known_hosts_path,
                            destination=sandbox_known_hosts,
                        ),
                    ],
                ),
                script_name="wrapper.py",
            )

        if not result.success:
            detail = result.error or result.stderr or "Unknown Git clone error"
            raise RegistryError(f"Sandboxed Git clone failed: {detail[:2000]}")
        try:
            clone_result = RegistryCloneResult.model_validate(result.output)
        except ValidationError as exc:
            raise RegistryError("Sandboxed Git clone returned invalid output") from exc

        clone_path = job_dir / "repo"
        if not clone_path.is_dir() or clone_path.is_symlink():
            raise RegistryError(
                "Sandboxed Git clone did not create a repository directory"
            )
        return clone_path, clone_result.commit_sha

    async def build_execution_artifact(
        self,
        *,
        package_path: Path,
        output_dir: Path,
        timeout_seconds: int,
    ) -> RegistryArtifactBuildResult:
        """Install a registry package in NsJail and pack its isolated output."""
        site_packages = await self.install_package(
            package_path=package_path,
            output_dir=output_dir,
            timeout_seconds=timeout_seconds,
        )
        return await self.package_site_packages(
            site_packages=site_packages,
            output_dir=output_dir,
            timeout_seconds=timeout_seconds,
        )

    async def install_package(
        self,
        *,
        package_path: Path,
        output_dir: Path,
        timeout_seconds: int,
    ) -> Path:
        """Install a registry package in a fresh networked jail."""
        job_dir = output_dir / "sandbox-install"
        source_dir = job_dir / "source"
        site_packages = job_dir / "cache" / "site-packages"
        private_cache = job_dir / "sandbox-cache"

        job_dir.mkdir(parents=True, exist_ok=True)
        site_packages.mkdir(parents=True, exist_ok=True)
        (private_cache / "uv-cache").mkdir(parents=True, exist_ok=True)

        # Preserve links instead of following them on the trusted host. Any link
        # resolution performed by the build backend then happens inside NsJail.
        shutil.copytree(package_path, source_dir, symlinks=True)
        (job_dir / "dependencies.json").write_text(
            json.dumps(["/work/source"]),
            encoding="utf-8",
        )
        (job_dir / "install.py").write_text(INSTALL_SCRIPT, encoding="utf-8")

        executor = NsjailExecutor(cache_dir=str(private_cache))
        result = await executor.execute_install(
            job_dir,
            _INSTALL_CACHE_KEY,
            timeout_seconds=timeout_seconds,
        )
        if not result.success:
            detail = result.error or result.stderr or "Unknown installation error"
            raise RegistryArtifactBuildError(
                f"Sandboxed registry package installation failed: {detail[:2000]}"
            )
        return site_packages

    async def package_site_packages(
        self,
        *,
        site_packages: Path,
        output_dir: Path,
        timeout_seconds: int,
    ) -> RegistryArtifactBuildResult:
        """Package installed files in a fresh no-network jail."""
        if not config.TRACECAT__REGISTRY_SYNC_SQUASHFS_ENABLED:
            raise RegistryArtifactBuildError(
                "Cannot build registry artifact because SquashFS is disabled"
            )
        if not site_packages.is_dir() or site_packages.is_symlink():
            raise RegistryArtifactBuildError(
                f"Installed site-packages directory does not exist: {site_packages}"
            )
        if not _SQUASHFS_MEMORY_PATTERN.fullmatch(
            config.TRACECAT__REGISTRY_SYNC_SQUASHFS_MEM
        ):
            raise RegistryArtifactBuildError("Invalid SquashFS memory configuration")

        job_dir = output_dir / "sandbox-package" / "work"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "script.py").write_text(_PACKAGING_SCRIPT, encoding="utf-8")
        (job_dir / "wrapper.py").write_text(WRAPPER_SCRIPT, encoding="utf-8")
        (job_dir / "inputs.json").write_text(
            json.dumps(
                {
                    "processors": config.TRACECAT__REGISTRY_SYNC_SQUASHFS_PROCESSORS,
                    "memory": config.TRACECAT__REGISTRY_SYNC_SQUASHFS_MEM,
                }
            ),
            encoding="utf-8",
        )

        executor = NsjailExecutor()
        result = await executor.execute(
            job_dir,
            SandboxConfig(
                network_enabled=False,
                resources=ResourceLimits(
                    timeout_seconds=timeout_seconds,
                    cpu_seconds=timeout_seconds,
                    memory_mb=2048,
                    max_file_size_mb=2048,
                ),
                bind_mounts=[
                    SandboxBindMount(
                        source=site_packages,
                        destination=Path("/input/site-packages"),
                    )
                ],
            ),
            script_name="wrapper.py",
        )
        if not result.success:
            detail = result.error or result.stderr or "Unknown packaging error"
            raise RegistryArtifactBuildError(
                f"Sandboxed registry packaging failed: {detail[:2000]}"
            )

        sandbox_artifact = job_dir / "site-packages.squashfs"
        try:
            artifact_stat = sandbox_artifact.lstat()
        except FileNotFoundError as exc:
            raise RegistryArtifactBuildError(
                "Sandboxed registry packaging did not create an artifact"
            ) from exc
        if not stat.S_ISREG(artifact_stat.st_mode):
            raise RegistryArtifactBuildError(
                "Sandboxed registry packaging did not create a regular file"
            )
        squashfs_path = output_dir / "site-packages.squashfs"
        squashfs_path.unlink(missing_ok=True)
        sandbox_artifact.replace(squashfs_path)
        content_hash = await asyncio.to_thread(_hash_file, squashfs_path)
        return RegistryArtifactBuildResult(
            squashfs_path=squashfs_path,
            squashfs_name=squashfs_path.name,
            content_hash=content_hash,
            artifact_size_bytes=squashfs_path.stat().st_size,
            site_packages_path=site_packages,
        )

    async def discover_actions(
        self,
        *,
        site_packages: Path,
        origin: str,
        package_name: str,
        repository_id: UUID,
        commit_sha: str | None,
        validate: bool,
        organization_id: UUID | None,
        timeout_seconds: int,
    ) -> SyncResultSuccess:
        """Import and serialize installed registry actions without network access."""
        # The install jail can write throughout its /work mount. Keep trusted
        # discovery scripts in a fresh host-created directory that was never
        # exposed to the untrusted installer, preventing pre-planted symlinks.
        with tempfile.TemporaryDirectory(
            prefix="tracecat_registry_discovery_"
        ) as temp_dir:
            discovery_root = Path(temp_dir)
            job_dir = discovery_root / "work"
            trusted_root = discovery_root / "trusted"
            job_dir.mkdir()
            trusted_root.mkdir()

            trusted_package_roots: list[Path] = []
            for index, package_path in enumerate(_trusted_runtime_package_paths()):
                package_root = trusted_root / str(index)
                shutil.copytree(
                    package_path,
                    package_root / package_path.name,
                    symlinks=True,
                )
                trusted_package_roots.append(package_root)

            inputs = {
                "origin": origin,
                "package_name": package_name,
                "repository_id": str(repository_id),
                "commit_sha": commit_sha,
                "validate": validate,
                "organization_id": str(organization_id) if organization_id else None,
            }
            (job_dir / "script.py").write_text(_DISCOVERY_SCRIPT, encoding="utf-8")
            (job_dir / "wrapper.py").write_text(WRAPPER_SCRIPT, encoding="utf-8")
            (job_dir / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")

            executor = NsjailExecutor()
            trusted_tracecat_root, *other_trusted_roots = trusted_package_roots
            result = await executor.execute(
                job_dir,
                SandboxConfig(
                    network_enabled=False,
                    resources=ResourceLimits(
                        timeout_seconds=timeout_seconds,
                        cpu_seconds=timeout_seconds,
                        memory_mb=2048,
                    ),
                    python_path_dirs=[
                        trusted_tracecat_root,
                        site_packages,
                        *_host_site_packages_paths(),
                        *other_trusted_roots,
                    ],
                ),
                script_name="wrapper.py",
            )
            if not result.success:
                detail = result.error or result.stderr or "Unknown discovery error"
                raise RegistryError(f"Sandboxed registry discovery failed: {detail}")

            parsed_result = SyncResultAdapter.validate_python(result.output)
            if isinstance(parsed_result, SyncResultError):
                raise RegistryError(parsed_result.error)
            return parsed_result
