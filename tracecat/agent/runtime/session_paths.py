"""Stable agent sandbox path helpers shared across runtime harnesses."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

JAILED_AGENT_HOME_DIR = Path("/home/agent")
JAILED_AGENT_JOB_DIR = Path("/run/tracecat/job")
JAILED_AGENT_WORK_DIR = Path("/work")


@dataclass(frozen=True, slots=True)
class AgentSandboxPathMapping:
    """Host and runtime-visible agent working directories for one session."""

    host_home_dir: Path
    host_work_dir: Path
    runtime_home_dir: Path
    runtime_work_dir: Path


def build_agent_sandbox_path_mapping(
    *,
    session_id: str,
    disable_nsjail: bool,
) -> AgentSandboxPathMapping:
    """Build stable host/runtime path mapping for one agent session."""
    session_root = Path(tempfile.gettempdir()) / f"tracecat-agent-{session_id}"
    host_home_dir = session_root / "agent-home"
    host_work_dir = session_root / "agent-work-dir"
    host_home_dir.mkdir(parents=True, exist_ok=True)
    host_work_dir.mkdir(parents=True, exist_ok=True)

    if disable_nsjail:
        runtime_home_dir = host_home_dir
        runtime_work_dir = host_work_dir
    else:
        runtime_home_dir = JAILED_AGENT_HOME_DIR
        runtime_work_dir = JAILED_AGENT_WORK_DIR

    return AgentSandboxPathMapping(
        host_home_dir=host_home_dir,
        host_work_dir=host_work_dir,
        runtime_home_dir=runtime_home_dir,
        runtime_work_dir=runtime_work_dir,
    )
