from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_ROOT = _REPO_ROOT / "tracecat"
_WORKFLOW_LOGGER_ALLOWLIST = {"tracecat/dsl/workflow_logging.py"}


def _runtime_python_files() -> list[Path]:
    return sorted(_RUNTIME_ROOT.rglob("*.py"))


def test_runtime_package_avoids_raw_loguru_logger_imports() -> None:
    violations: list[str] = []
    pattern = re.compile(r"\bfrom loguru import logger\b")

    for path in _runtime_python_files():
        relpath = path.relative_to(_REPO_ROOT).as_posix()
        if relpath.startswith("tracecat/logger/"):
            continue
        if pattern.search(path.read_text()):
            violations.append(relpath)

    assert violations == []


def test_runtime_package_avoids_direct_workflow_logger_usage() -> None:
    violations: list[str] = []
    pattern = re.compile(r"\bworkflow\.logger\.")

    for path in _runtime_python_files():
        relpath = path.relative_to(_REPO_ROOT).as_posix()
        if relpath in _WORKFLOW_LOGGER_ALLOWLIST:
            continue
        if pattern.search(path.read_text()):
            violations.append(relpath)

    assert violations == []
