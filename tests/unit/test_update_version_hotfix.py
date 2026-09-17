"""Exercise hotfix version updates through the release script."""

import os
import subprocess
from pathlib import Path

import pytest
from packaging.version import Version

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/update-version.sh"
BASE = "1.0.0-beta.52-rc.22"
PYTHON_BASE = "1.0.0b52+rc.22"
VERSION_FILES = (
    "tracecat/__init__.py",
    "packages/tracecat-registry/tracecat_registry/__init__.py",
)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in VERSION_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'__version__ = "{BASE}"\n__pep440_version__ = "{PYTHON_BASE}"\n'
        )
    (tmp_path / "docker-compose.yml").write_text(
        f"image: ghcr.io/tracecathq/tracecat:${{TRACECAT__IMAGE_TAG:-{BASE}}}\n"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "install.mdx").write_text(
        f"https://raw.githubusercontent.com/TracecatHQ/tracecat/{BASE}/env.sh\n"
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    # The release script copies its output; tests must not change the clipboard.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("pbcopy", "xclip", "wl-copy"):
        command = bin_dir / name
        command.write_text("#!/bin/sh\ncat > /dev/null\n")
        command.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


@pytest.mark.parametrize(
    ("target", "python_version", "incremented", "incremented_python"),
    [
        (
            f"{BASE}.post9",
            f"{PYTHON_BASE}.post9",
            f"{BASE}.post10",
            f"{PYTHON_BASE}.post10",
        ),
        (
            "1.0.0-beta.52.post1",
            "1.0.0b52.post1",
            "1.0.0-beta.52.post2",
            "1.0.0b52.post2",
        ),
        (
            "1.0.0-beta.52-rc.23",
            "1.0.0b52+rc.23",
            "1.0.0-beta.52-rc.24",
            "1.0.0b52+rc.24",
        ),
    ],
)
def test_update_hotfix_and_increment(
    checkout: Path,
    target: str,
    python_version: str,
    incremented: str,
    incremented_python: str,
) -> None:
    for args, public, package in (
        ([target], target, python_version),
        ([], incremented, incremented_python),
        (["1.0.0-beta.53"], "1.0.0-beta.53", "1.0.0b53"),
    ):
        subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=checkout,
            input="y\n",
            text=True,
            capture_output=True,
            check=True,
        )
        for name in VERSION_FILES:
            assert (checkout / name).read_text() == (
                f'__version__ = "{public}"\n__pep440_version__ = "{package}"\n'
            )
        assert Version(package)
        assert (checkout / "docker-compose.yml").read_text() == (
            f"image: ghcr.io/tracecathq/tracecat:${{TRACECAT__IMAGE_TAG:-{public}}}\n"
        )
        assert (checkout / "docs/install.mdx").read_text() == (
            f"https://raw.githubusercontent.com/TracecatHQ/tracecat/{public}/env.sh\n"
        )


@pytest.mark.parametrize("suffix", [".post", ".post-1", ".post1.extra"])
def test_reject_invalid_hotfix(checkout: Path, suffix: str) -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), BASE + suffix],
        cwd=checkout,
        input="y\n",
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    subprocess.run(["git", "diff", "--exit-code"], cwd=checkout, check=True)
