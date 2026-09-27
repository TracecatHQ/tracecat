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


def test_alpha_hotfix_replaces_complete_tags_in_tracked_files(checkout: Path) -> None:
    seed_versions(checkout, "1.2.0-alpha.1.1", "1.2.0a1.post1")
    # Different old versions exercise pattern replacement, not just literal replacement.
    content = (
        "${TRACECAT__IMAGE_TAG:-1.2.0-alpha.1.9}\n"
        "https://raw.githubusercontent.com/TracecatHQ/tracecat/1.2.0-alpha.1.9/file\n"
        "https://github.com/TracecatHQ/tracecat/blob/1.2.0-alpha.1.9/file\n"
        "`1.2.0-alpha.1.9`\n"
        "TF_VAR_tracecat_image_tag=1.2.0-alpha.1.9\n"
        "Current version: 1.2.0-alpha.1.1\n"
    )
    doc = checkout / "docs/install.md"
    doc.parent.mkdir()
    doc.write_text(content)
    subprocess.run(["git", "add", "--", "docs/install.md"], cwd=checkout, check=True)

    result = update(checkout, "1.2.0-alpha.1.2")

    assert result.returncode == 0, result.stdout + result.stderr
    assert doc.read_text() == content.replace(
        "1.2.0-alpha.1.9", "1.2.0-alpha.1.2"
    ).replace("1.2.0-alpha.1.1", "1.2.0-alpha.1.2")


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
