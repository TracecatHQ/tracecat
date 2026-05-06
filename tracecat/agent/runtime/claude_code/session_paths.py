"""Stable Claude session path helpers shared across runtime execution paths."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ClaudeSandboxPathMapping:
    """Host and runtime-visible Claude working directories for one turn."""

    host_home_dir: Path
    host_project_dir: Path
    runtime_home_dir: Path
    runtime_cwd: Path


def build_claude_sandbox_path_mapping(
    *,
    session_id: str,
    disable_nsjail: bool,
) -> ClaudeSandboxPathMapping:
    """Build stable host/runtime path mapping for one Claude session."""
    session_root = Path(tempfile.gettempdir()) / f"tracecat-agent-{session_id}"
    host_home_dir = session_root / "claude-home"
    host_project_dir = session_root / "claude-project"
    host_home_dir.mkdir(parents=True, exist_ok=True)
    host_project_dir.mkdir(parents=True, exist_ok=True)

    if disable_nsjail:
        runtime_home_dir = host_home_dir
        runtime_cwd = host_project_dir
    else:
        runtime_home_dir = Path("/work/claude-home")
        runtime_cwd = Path("/work/claude-project")

    return ClaudeSandboxPathMapping(
        host_home_dir=host_home_dir,
        host_project_dir=host_project_dir,
        runtime_home_dir=runtime_home_dir,
        runtime_cwd=runtime_cwd,
    )
