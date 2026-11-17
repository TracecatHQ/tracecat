"""Exercise the release version updater in disposable git checkouts."""

import os
import subprocess
from pathlib import Path

import pytest
from packaging.version import Version

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/update-version.sh"
VERSION_FILES = (
    "tracecat/__init__.py",
    "packages/tracecat-registry/tracecat_registry/__init__.py",
)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    # Avoid touching the developer's clipboard after a successful update.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    clipboard = bin_dir / "pbcopy"
    clipboard.write_text("#!/bin/sh\ncat >/dev/null\n")
    clipboard.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def seed_versions(checkout: Path, public: str, python: str) -> None:
    for name in VERSION_FILES:
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'__version__ = "{public}"\n__pep440_version__ = "{python}"\n')
    subprocess.run(["git", "add", "--", *VERSION_FILES], cwd=checkout, check=True)


def update(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=checkout,
        input="y\n",
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("current", "current_python", "args", "expected", "expected_python"),
    [
        (
            "1.2.0-alpha.1",
            "1.2.0a1",
            ["1.2.0-alpha.1.1"],
            "1.2.0-alpha.1.1",
            "1.2.0a1.post1",
        ),
        (
            "1.2.0-alpha.1.1",
            "1.2.0a1.post1",
            ["1.2.0-alpha.1.2"],
            "1.2.0-alpha.1.2",
            "1.2.0a1.post2",
        ),
        ("1.2.0-alpha.1.2", "1.2.0a1.post2", [], "1.2.0-alpha.1.3", "1.2.0a1.post3"),
        (
            "1.2.0-alpha.1.2",
            "1.2.0a1.post2",
            ["1.2.0-alpha.2"],
            "1.2.0-alpha.2",
            "1.2.0a2",
        ),
        ("1.2.0-alpha.1.2", "1.2.0a1.post2", ["--rc"], "1.2.0-rc.0", "1.2.0rc0"),
        ("1.2.0-alpha.1.2", "1.2.0a1.post2", ["--release"], "1.2.0", "1.2.0"),
        ("1.2.0-alpha.1", "1.2.0a1", [], "1.2.0-alpha.2", "1.2.0a2"),
        (
            "1.2.0-beta.48-rc.5",
            "1.2.0b48+rc.5",
            [],
            "1.2.0-beta.48-rc.6",
            "1.2.0b48+rc.6",
        ),
        ("1.2.0", "1.2.0", [], "1.2.1", "1.2.1"),
    ],
)
def test_version_bump(
    checkout: Path,
    current: str,
    current_python: str,
    args: list[str],
    expected: str,
    expected_python: str,
) -> None:
    seed_versions(checkout, current, current_python)

    result = update(checkout, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(Version(expected_python)) == expected_python
    for name in VERSION_FILES:
        assert (checkout / name).read_text() == (
            f'__version__ = "{expected}"\n__pep440_version__ = "{expected_python}"\n'
        )


def test_only_tracecat_version_fields_change(checkout: Path) -> None:
    """Only real Tracecat version pins change; docs and other strings stay put.

    Docs and README TF_VAR examples are not bumped, and a generic current
    version such as 1.0.0 also appears in Terraform required_version,
    third-party package pins, spec URLs, and prose examples. Untracked and
    symlinked Terraform files are never rewritten.
    """
    seed_versions(checkout, "1.0.0", "1.0.0")
    decoys = (
        "export TF_VAR_tracecat_image_tag=1.0.0\n"
        "export TF_VAR_tracecat_image_tag=1.1.0\n"
        "Spec: https://www.conventionalcommits.org/en/v1.0.0/\n"
        "Example: `1.0.0` and (`>1.0.0-beta.xx`)\n"
        "Run `npx package@1.0.0`.\n"
        "Blob: https://github.com/TracecatHQ/tracecat/blob/1.0.0/file\n"
        "${TRACECAT__IMAGE_TAG:-1.0.0}\n"
    )
    # "{v}" marks a real pin: seeded with the current version, expected bumped.
    files = {
        ".github/ISSUE_TEMPLATE/bug_report.md": (
            "- Tracecat version (e.g. `{v}`)\n- Browser version (e.g. `1.0.0`)\n"
        ),
        "deployments/fargate/variables.tf": (
            'variable "tracecat_image_tag" {\n  default = "{v}"\n}\n'
            'variable "other" {\n  default = "1.0.0"\n}\n'
        ),
        "deployments/fargate/modules/ecs/variables.tf": (
            'variable "tracecat_image_tag" {\n  type    = string\n  default = "{v}"\n}\n'
        ),
        "deployments/fargate/modules/network/main.tf": (
            'terraform {\n  required_version = ">= 1.0.0"\n}\n'
        ),
        "deployments/fargate/README.md": decoys,
        "docs/self-hosting/aws-fargate.mdx": decoys,
        "docs/tools/github.mdx": decoys,
        "CONTRIBUTING.md": decoys,
        "docker-compose.yml": decoys,
    }
    for name, template in files.items():
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template.replace("{v}", "1.0.0"))
    pin = 'variable "tracecat_image_tag" {\n  default = "1.0.0"\n}\n'
    untracked = checkout / "deployments/fargate/modules/untracked/variables.tf"
    untracked.parent.mkdir(parents=True)
    untracked.write_text(pin)
    target = checkout / "shared/image.tf"
    target.parent.mkdir()
    target.write_text(pin)
    link = checkout / "deployments/fargate/modules/linked/variables.tf"
    link.parent.mkdir(parents=True)
    link.symlink_to("../../../../shared/image.tf")
    tracked = [
        *files,
        "shared/image.tf",
        "deployments/fargate/modules/linked/variables.tf",
    ]
    subprocess.run(["git", "add", "--", *tracked], cwd=checkout, check=True)
    # Forced color would wrap discovered paths in ANSI escapes.
    subprocess.run(["git", "config", "color.ui", "always"], cwd=checkout, check=True)

    result = update(checkout, "1.4.0-alpha.4")

    assert result.returncode == 0, result.stdout + result.stderr
    for name, template in files.items():
        expected = template.replace("{v}", "1.4.0-alpha.4")
        assert (checkout / name).read_text() == expected, name
    assert untracked.read_text() == pin
    assert link.is_symlink()
    assert target.read_text() == pin


@pytest.mark.parametrize(
    "version", ["1.2.0-alpha.1.2.3", "1.2.0-alpha.1.", "1.2.0-alpha.1.2-rc.1"]
)
def test_malformed_alpha_hotfix_does_not_modify_files(
    checkout: Path, version: str
) -> None:
    seed_versions(checkout, "1.2.0-alpha.1", "1.2.0a1")
    before = [(checkout / name).read_text() for name in VERSION_FILES]

    result = update(checkout, version)

    assert result.returncode != 0
    assert "Version must use the release tag format" in result.stdout
    assert [(checkout / name).read_text() for name in VERSION_FILES] == before
