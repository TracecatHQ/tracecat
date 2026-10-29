"""Exercise the main-branch stable reference sync without touching GitHub."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/sync_stable_refs.py"
WORKFLOW = ROOT / ".github/workflows/sync-stable-refs.yml"

COMPOSE = "services:\n  api:\n    image: ghcr.io/tracecathq/tracecat:${{TRACECAT__IMAGE_TAG:-{v}}}\n"
FILES = {
    "docker-compose.yml": COMPOSE,
    "docker-compose.dev.yml": "    # image: ghcr.io/tracecathq/tracecat-ui:${{TRACECAT__IMAGE_TAG:-{v}}}\n",
    "docs/self-hosting/docker-compose.mdx": (
        "curl -o docker-compose.yml "
        "https://raw.githubusercontent.com/TracecatHQ/tracecat/{d}/docker-compose.yml\n"
    ),
    "deployments/fargate/variables.tf": (
        'variable "tracecat_image_tag" {{\n  type    = string\n  default = "{v}"\n}}\n'
    ),
    "deployments/fargate/README.md": (
        "Terraform stack (`>{v}-beta.xx`).\nexport TF_VAR_tracecat_image_tag={v}\n"
    ),
    ".github/ISSUE_TEMPLATE/bug_report.md": "- Tracecat version (e.g. `{v}`)\n",
}
UNRELATED = {
    "deployments/fargate/modules/network/main.tf": 'required_version = ">= {v}"\n',
    "tracecat/__init__.py": '__version__ = "{v}"\n',
    "docs/changelog.mdx": "Released {v}.\n",
}


def seed(root: Path, version: str, docs: str | None = None) -> None:
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    for name, template in {**FILES, **UNRELATED}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template.format(v=version, d=docs or version))
    (root / "docs/logo.png").write_bytes(b"\x89PNG\r\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    (root / "docs/untracked.mdx").write_text(
        FILES["docs/self-hosting/docker-compose.mdx"].format(d=version)
    )


def sync(root: Path, version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), version, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_updates_only_stable_install_references(tmp_path: Path) -> None:
    seed(tmp_path, "1.0.0", docs="1.1.0")

    result = sync(tmp_path, "1.2.3")

    assert result.returncode == 0, result.stderr
    assert sorted(result.stdout.split()) == sorted(FILES)
    for name, template in FILES.items():
        assert (tmp_path / name).read_text() == template.format(v="1.2.3", d="1.2.3")
    for name, template in UNRELATED.items():
        assert (tmp_path / name).read_text() == template.format(v="1.0.0")
    assert "/1.0.0/" in (tmp_path / "docs/untracked.mdx").read_text()


@pytest.mark.parametrize(
    ("current", "target", "moves"),
    [
        ("1.2.3", "1.2.3", False),
        ("1.3.0", "1.2.4", False),
        ("1.2.3", "1.10.0", True),
        ("1.3.0-alpha.1", "1.3.0", True),
        ("1.3.0-alpha.1", "1.2.9", False),
    ],
)
def test_never_moves_main_backwards(
    tmp_path: Path, current: str, target: str, moves: bool
) -> None:
    seed(tmp_path, current)

    result = sync(tmp_path, target)

    assert result.returncode == 0, result.stderr
    assert bool(result.stdout) is moves
    expected = target if moves else current
    assert (tmp_path / "docker-compose.yml").read_text() == COMPOSE.format(v=expected)


@pytest.mark.parametrize("version", ["1.3.0-alpha.5", "v1.2.3", "01.2.3", "1.2", ""])
def test_rejects_non_stable_versions(tmp_path: Path, version: str) -> None:
    seed(tmp_path, "1.0.0")

    result = sync(tmp_path, version)

    assert result.returncode == 2
    assert (tmp_path / "docker-compose.yml").read_text() == COMPOSE.format(v="1.0.0")


def test_repo_references_all_match_compose_default() -> None:
    """Every reference the sync owns must agree, or hand edits have drifted."""
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import sync_stable_refs as refs
    finally:
        sys.path.remove(str(SCRIPT.parent))

    current = refs.current_version(ROOT)
    stale = []
    for path in refs.tracked_files(ROOT):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for pattern, replacement in refs.SUBSTITUTIONS:
            if pattern.sub(replacement.format(v=current), text) != text:
                stale.append(str(path.relative_to(ROOT)))
    assert not stale, f"References disagree with {current}: {stale}"


GH_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$GH_LOG"
case "$1 $2" in
  "api repos/acme/tracecat/releases/latest") echo "$LATEST" ;;
  "api repos/acme/tracecat/git/ref/"*) [[ "$BRANCH_EXISTS" == true ]] ;;
  "api graphql") cp "$4" "$GH_DIR/commit.json" ;;
  "pr create") cp "${@: -1}" "$GH_DIR/body.md" ;;
esac
"""


def steps() -> dict[str, dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return {step.get("name", ""): step for step in workflow["jobs"]["sync"]["steps"]}


def run_step(
    name: str, cwd: Path, tmp_path: Path, **env: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    output = tmp_path / "output"
    output.write_text("")
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", steps()[name]["run"]],
        cwd=cwd,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "REPO": "acme/tracecat",
            "GH_LOG": str(tmp_path / "gh.log"),
            "GH_DIR": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(runner_temp),
            "LATEST": "1.2.3",
            "BRANCH_EXISTS": "false",
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_workflow_proposes_latest_stable_once(tmp_path: Path) -> None:
    result, output = run_step("Resolve latest stable release", tmp_path, tmp_path)
    assert result.returncode == 0, result.stderr
    assert output == {
        "sync": "true",
        "version": "1.2.3",
        "branch": "ci/sync-stable-refs-1.2.3",
    }

    result, output = run_step(
        "Resolve latest stable release", tmp_path, tmp_path, BRANCH_EXISTS="true"
    )
    assert result.returncode == 0, result.stderr
    assert output == {"sync": "false"}


@pytest.mark.parametrize("latest", ["1.3.0-alpha.5", "1.2.3\nx", "../main", ""])
def test_workflow_rejects_non_stable_latest(tmp_path: Path, latest: str) -> None:
    result, output = run_step(
        "Resolve latest stable release", tmp_path, tmp_path, LATEST=latest
    )
    assert result.returncode != 0
    assert output == {}


def seed_checkout(tmp_path: Path, version: str) -> Path:
    repo = tmp_path / "repo"
    seed(repo, version)
    (repo / "scripts").mkdir()
    (repo / "scripts/sync_stable_refs.py").write_text(SCRIPT.read_text())
    return repo


def test_workflow_opens_signed_api_commit_and_pr(tmp_path: Path) -> None:
    repo = seed_checkout(tmp_path, "1.0.0")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "seed",
        ],
        cwd=repo,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    result, output = run_step(
        "Update stable references", repo, tmp_path, VERSION="1.2.3"
    )
    assert result.returncode == 0, result.stderr
    assert output == {"changed": "true"}

    result, _ = run_step(
        "Open bump PR",
        repo,
        tmp_path,
        VERSION="1.2.3",
        BRANCH="ci/sync-stable-refs-1.2.3",
    )
    assert result.returncode == 0, result.stderr

    log = (tmp_path / "gh.log").read_text().splitlines()
    assert log[0] == (
        "api repos/acme/tracecat/git/refs "
        f"-f ref=refs/heads/ci/sync-stable-refs-1.2.3 -f sha={base}"
    )
    assert log[-1].startswith(
        "pr create --repo acme/tracecat --base main "
        "--head ci/sync-stable-refs-1.2.3 --title release: sync main to stable 1.2.3"
    )
    commit = json.loads((tmp_path / "commit.json").read_text())["variables"]["input"]
    assert commit["expectedHeadOid"] == base
    assert commit["branch"] == {
        "repositoryNameWithOwner": "acme/tracecat",
        "branchName": "ci/sync-stable-refs-1.2.3",
    }
    additions = {
        a["path"]: base64.b64decode(a["contents"]).decode()
        for a in commit["fileChanges"]["additions"]
    }
    assert additions == {
        name: template.format(v="1.2.3", d="1.2.3") for name, template in FILES.items()
    }
    body = (tmp_path / "body.md").read_text()
    assert "- `docker-compose.yml`" in body
    assert "releases/tag/1.2.3" in body


def test_no_changes_skips_pr(tmp_path: Path) -> None:
    repo = seed_checkout(tmp_path, "1.2.3")
    result, output = run_step(
        "Update stable references", repo, tmp_path, VERSION="1.2.3"
    )
    assert result.returncode == 0, result.stderr
    assert output == {"changed": "false"}
    assert steps()["Open bump PR"]["if"] == "steps.update.outputs.changed == 'true'"


def test_workflow_trust_boundary() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow[True]
    assert set(triggers) == {"schedule"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["sync"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert workflow["concurrency"]["cancel-in-progress"] is False
    checkout = workflow["jobs"]["sync"]["steps"][0]
    assert checkout["with"] == {"ref": "main", "persist-credentials": False}
    for step in workflow["jobs"]["sync"]["steps"]:
        if "uses" in step:
            assert len(step["uses"].split("@", 1)[1].split()[0]) == 40
