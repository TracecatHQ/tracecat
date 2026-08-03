from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
CLUSTER_SCRIPT = REPO_ROOT / "scripts" / "cluster"


def _current_worktree_id() -> str:
    """Mirror ``get_worktree_id`` in ``scripts/cluster``.

    The script names clusters after the checkout it runs in: ``main`` for the
    primary worktree, otherwise the sanitized branch name. Tests derive it the
    same way instead of assuming ``main``, so they pass when the suite is run
    from a linked worktree.
    """

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git_dir = Path(_git("rev-parse", "--absolute-git-dir")).resolve()
    common_raw = Path(_git("rev-parse", "--git-common-dir"))
    common_dir = (
        common_raw if common_raw.is_absolute() else REPO_ROOT / common_raw
    ).resolve()

    if git_dir == common_dir:
        return "main"

    branch = _git("rev-parse", "--abbrev-ref", "HEAD").lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", branch)).strip("-")


CURRENT_WORKTREE_ID = _current_worktree_id()


def _compose_project(name: str, config_files: str) -> dict[str, str]:
    return {
        "Name": name,
        "Status": "running(15)",
        "ConfigFiles": config_files,
    }


@pytest.fixture
def fake_docker_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

printf '%s\\n' "$*" >> "$MOCK_DOCKER_LOG"

if [[ "${1:-}" == "compose" && "${2:-}" == "ls" ]]; then
    printf '%s\\n' "${MOCK_COMPOSE_LS_JSON:-[]}"
    exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
    for arg in "$@"; do
        if [[ "$arg" == "down" ]]; then
            exit 0
        fi
    done
fi

if [[ "${1:-}" == "volume" && "${2:-}" == "ls" ]]; then
    project=""
    for arg in "$@"; do
        if [[ "$arg" == label=com.docker.compose.project=* ]]; then
            project="${arg#label=com.docker.compose.project=}"
        fi
    done
    while IFS='|' read -r volume_project volume; do
        if [[ "$volume_project" == "$project" && -n "$volume" ]]; then
            printf '%s\\n' "$volume"
        fi
    done <<< "${MOCK_PROJECT_VOLUMES:-}"
    exit 0
fi

if [[ "${1:-}" == "volume" && "${2:-}" == "rm" ]]; then
    exit 0
fi

echo "Unexpected docker invocation: $*" >&2
exit 1
"""
    )
    docker.chmod(0o755)
    return bin_dir


def _run_cluster(
    fake_docker_bin: Path,
    compose_projects: list[dict[str, str]],
    *args: str,
    project_volumes: dict[str, list[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    registry_dir = fake_docker_bin.parent / "registry"
    docker_log = fake_docker_bin.parent / "docker.log"
    volume_lines = [
        f"{project}|{volume}"
        for project, volumes in (project_volumes or {}).items()
        for volume in volumes
    ]
    env.update(
        {
            "PATH": f"{fake_docker_bin}{os.pathsep}{env['PATH']}",
            "PORTLESS": "0",
            "TRACECAT__USE_PORTLESS": "0",
            "TRACECAT_CLUSTER_REGISTRY_DIR": str(registry_dir),
            "MOCK_COMPOSE_LS_JSON": json.dumps(compose_projects),
            "MOCK_DOCKER_LOG": str(docker_log),
            "MOCK_PROJECT_VOLUMES": "\n".join(volume_lines),
        }
    )
    return subprocess.run(
        [str(CLUSTER_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _registry_dir(fake_docker_bin: Path) -> Path:
    return fake_docker_bin.parent / "registry"


def _docker_invocations(fake_docker_bin: Path) -> list[str]:
    docker_log = fake_docker_bin.parent / "docker.log"
    if not docker_log.exists():
        return []
    return docker_log.read_text().splitlines()


def _write_registry_entry(
    fake_docker_bin: Path,
    *,
    project: str,
    worktree_path: Path,
    worktree_id: str,
    cluster_num: int,
    portless_alias: str = "",
) -> Path:
    registry_dir = _registry_dir(fake_docker_bin)
    registry_dir.mkdir()
    registry_file = registry_dir / f"{project}.env"
    fields = {
        "PROJECT": project,
        "WORKTREE_PATH": str(worktree_path),
        "WORKTREE_ID": worktree_id,
        "CLUSTER_NUM": str(cluster_num),
        "PORTLESS_ALIAS": portless_alias,
        "CREATED_AT": "2026-07-24T12:00:00Z",
    }
    registry_file.write_text(
        "".join(f"{key}={shlex.quote(value)}\n" for key, value in fields.items())
    )
    return registry_file


def test_bare_cluster_lists_projects_from_all_worktrees(
    fake_docker_bin: Path,
) -> None:
    projects = [
        _compose_project(
            "tracecat-feature-a-2",
            "/tmp/tracecat-feature-a/docker-compose.dev.yml,"
            "/tmp/tracecat-feature-a/docker-compose.sandbox.yml",
        ),
        _compose_project(
            "unrelated-project",
            "/tmp/unrelated/docker-compose.yml",
        ),
        _compose_project(
            "tracecat-main-7",
            str(REPO_ROOT / "docker-compose.dev.yml"),
        ),
    ]

    result = _run_cluster(fake_docker_bin, projects)

    assert result.returncode == 0, result.stderr
    assert (
        f"Running Tracecat clusters (current worktree: {CURRENT_WORKTREE_ID}):"
        in result.stdout
    )
    assert "[2] tracecat-feature-a-2: http://localhost:180" in result.stdout
    assert "source: /tmp/tracecat-feature-a" in result.stdout
    assert "[7] tracecat-main-7: http://localhost:680 (this worktree)" in result.stdout
    assert "unrelated-project" not in result.stdout
    assert "Usage:" not in result.stdout

    list_result = _run_cluster(fake_docker_bin, projects, "list")
    assert list_result.returncode == 0, list_result.stderr
    assert "tracecat-feature-a-2" in list_result.stdout
    assert "tracecat-main-7" in list_result.stdout


def test_cluster_help_remains_available(fake_docker_bin: Path) -> None:
    result = _run_cluster(fake_docker_bin, [], "--help")

    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert (
        "With no arguments: lists running clusters across all worktrees"
        in result.stdout
    )


def test_status_commands_resolve_clusters_across_worktrees(
    fake_docker_bin: Path,
) -> None:
    projects = [
        _compose_project(
            "tracecat-feature-a-2",
            "/tmp/tracecat-feature-a/docker-compose.dev.yml",
        )
    ]

    explicit_result = _run_cluster(fake_docker_bin, projects, "2", "ports")
    assert explicit_result.returncode == 0, explicit_result.stderr
    assert "Cluster feature-a-2 port mappings:" in explicit_result.stdout
    assert "UI (Caddy):      http://localhost:180" in explicit_result.stdout

    automatic_result = _run_cluster(fake_docker_bin, projects, "ports")
    assert automatic_result.returncode == 0, automatic_result.stderr
    assert "Cluster feature-a-2 port mappings:" in automatic_result.stdout


def test_unqualified_mutating_command_does_not_target_another_worktree(
    fake_docker_bin: Path,
) -> None:
    projects = [
        _compose_project(
            "tracecat-feature-a-2",
            "/tmp/tracecat-feature-a/docker-compose.dev.yml",
        )
    ]

    result = _run_cluster(fake_docker_bin, projects, "down")

    assert result.returncode == 1
    assert (
        f"No clusters are running for worktree '{CURRENT_WORKTREE_ID}'" in result.stderr
    )
    assert "pass an explicit cluster number" in result.stderr


def test_reap_leaves_registered_existing_worktree_alone(
    fake_docker_bin: Path,
) -> None:
    registry_file = _write_registry_entry(
        fake_docker_bin,
        project="tracecat-main-1",
        worktree_path=REPO_ROOT,
        worktree_id="main",
        cluster_num=1,
    )

    result = _run_cluster(fake_docker_bin, [], "reap")

    assert result.returncode == 0, result.stderr
    assert registry_file.exists()
    assert "reaped 0, alive 1" in result.stdout
    assert not any(" down " in call for call in _docker_invocations(fake_docker_bin))


def test_reap_tears_down_missing_worktree_and_deletes_registry(
    fake_docker_bin: Path,
) -> None:
    project = "tracecat-deleted-2"
    registry_file = _write_registry_entry(
        fake_docker_bin,
        project=project,
        worktree_path=fake_docker_bin.parent / "deleted worktree",
        worktree_id="deleted",
        cluster_num=2,
    )

    result = _run_cluster(
        fake_docker_bin,
        [_compose_project(project, "/tmp/deleted/docker-compose.dev.yml")],
        "reap",
    )

    assert result.returncode == 0, result.stderr
    assert (
        f"compose -p {project} down --volumes --remove-orphans"
        in _docker_invocations(fake_docker_bin)
    )
    assert not registry_file.exists()
    assert "reaped 1, alive 0" in result.stdout


def test_reap_removes_leftover_labelled_volumes(
    fake_docker_bin: Path,
) -> None:
    project = "tracecat-deleted-3"
    volume = f"{project}_postgres_db_data"
    _write_registry_entry(
        fake_docker_bin,
        project=project,
        worktree_path=fake_docker_bin.parent / "missing",
        worktree_id="deleted",
        cluster_num=3,
    )

    result = _run_cluster(
        fake_docker_bin,
        [],
        "reap",
        project_volumes={project: [volume]},
    )

    assert result.returncode == 0, result.stderr
    invocations = _docker_invocations(fake_docker_bin)
    assert (
        "volume ls "
        f"--filter label=com.docker.compose.project={project} --format {{{{.Name}}}}"
        in invocations
    )
    assert f"volume rm {volume}" in invocations


def test_reap_dry_run_performs_zero_docker_mutations(
    fake_docker_bin: Path,
) -> None:
    project = "tracecat-deleted-4"
    volume = f"{project}_redis_data"
    registry_file = _write_registry_entry(
        fake_docker_bin,
        project=project,
        worktree_path=fake_docker_bin.parent / "missing worktree",
        worktree_id="deleted",
        cluster_num=4,
        portless_alias="c4.deleted.tracecat",
    )

    result = _run_cluster(
        fake_docker_bin,
        [],
        "reap",
        "--dry-run",
        project_volumes={project: [volume]},
    )

    assert result.returncode == 0, result.stderr
    invocations = _docker_invocations(fake_docker_bin)
    assert not any(" down " in call for call in invocations)
    assert not any(call.startswith("volume rm ") for call in invocations)
    assert registry_file.exists()
    assert (
        f"[dry-run] docker compose -p {project} down --volumes --remove-orphans"
        in result.stdout
    )
    assert f"[dry-run] docker volume rm {volume}" in result.stdout


def test_reap_warns_but_does_not_remove_unregistered_project(
    fake_docker_bin: Path,
) -> None:
    project = "tracecat-legacy-5"

    result = _run_cluster(
        fake_docker_bin,
        [_compose_project(project, "/tmp/legacy/docker-compose.dev.yml")],
        "reap",
    )

    assert result.returncode == 0, result.stderr
    assert f"Warning: {project} is unregistered, not reaped" in result.stdout
    assert not any(" down " in call for call in _docker_invocations(fake_docker_bin))
