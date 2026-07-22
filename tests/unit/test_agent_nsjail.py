from __future__ import annotations

import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import tracecat.agent.sandbox.nsjail as nsjail_module
from tracecat.agent.sandbox.config import AGENT_CLAUDE_UID, AGENT_TOOL_UID

_TOOL_IDENTITY_PROBE = r"""
import errno
import json
import os
import pwd
from pathlib import Path

def denied(operation):
    try:
        operation()
    except OSError as exc:
        return exc.errno in {errno.EACCES, errno.EPERM}
    return False

status = {}
for line in Path('/proc/self/status').read_text().splitlines():
    key, separator, value = line.partition(':')
    if separator:
        status[key] = value.strip()

private = Path('/home/agent')
tool_home = Path('/home/tools')
work = Path('/work')
(tool_home / 'tool.txt').write_text('tool-private')
(work / 'from-tool.txt').write_text('tool-shared')
result = {
    'uid': os.getuid(),
    'euid': os.geteuid(),
    'gid': os.getgid(),
    'user': pwd.getpwuid(os.getuid()).pw_name,
    'caps': {name: status[name] for name in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb')},
    'no_new_privs': status['NoNewPrivs'],
    'home': os.environ['HOME'],
    'tmpdir': os.environ['TMPDIR'],
    'read_work': (work / 'from-claude.txt').read_text(),
    'private_denials': {
        'stat': denied(lambda: (private / 'stat.txt').stat()),
        'read': denied(lambda: (private / 'read.txt').read_text()),
        'write': denied(lambda: (private / 'write.txt').write_text('tampered')),
        'rename': denied(lambda: (private / 'rename.txt').rename(work / 'stolen.txt')),
        'delete': denied(lambda: (private / 'delete.txt').unlink()),
    },
}
print(json.dumps(result))
"""


_CLAUDE_IDENTITY_PROBE = r"""
import base64
import json
import os
import subprocess
from pathlib import Path

home = Path('/home/agent')
work = Path('/work')
for name in ('stat.txt', 'read.txt', 'write.txt', 'rename.txt', 'delete.txt'):
    (home / name).write_text(name)
(work / 'from-claude.txt').write_text('claude-shared')

payload = base64.urlsafe_b64encode(json.dumps({
    'argv': ['/usr/local/bin/python3', '-c', TOOL_SCRIPT],
    'env': {},
}).encode()).decode()
completed = subprocess.run(
    [
        '/usr/local/bin/python3',
        '-I',
        '/run/tracecat/job/shim_entrypoint.py',
        '--tracecat-tool-launch',
        payload,
    ],
    capture_output=True,
    text=True,
    check=False,
)

tool_home_denied = False
try:
    (Path('/home/tools') / 'tool.txt').read_text()
except PermissionError:
    tool_home_denied = True

status = {}
for line in Path('/proc/self/status').read_text().splitlines():
    key, separator, value = line.partition(':')
    if separator:
        status[key] = value.strip()

print(json.dumps({
    'uid': os.getuid(),
    'euid': os.geteuid(),
    'caps': {name: status[name] for name in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb')},
    'no_new_privs': status['NoNewPrivs'],
    'tool_returncode': completed.returncode,
    'tool_stderr': completed.stderr,
    'tool': json.loads(completed.stdout) if completed.returncode == 0 else None,
    'tool_home_denied': tool_home_denied,
    'read_work': (work / 'from-tool.txt').read_text() if completed.returncode == 0 else None,
    'private_unchanged': {
        name: (home / name).read_text() == name
        for name in ('stat.txt', 'read.txt', 'write.txt', 'rename.txt', 'delete.txt')
    },
    'stolen_exists': (work / 'stolen.txt').exists(),
}))
"""


def test_runtime_directories_use_private_home_and_shared_setgid_work(
    tmp_path: Path,
) -> None:
    session_home = tmp_path / "agent-home"
    session_work = tmp_path / "agent-work"

    nsjail_module._prepare_runtime_directories(
        session_home_dir=session_home,
        session_work_dir=session_work,
    )

    assert stat.S_IMODE(session_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(session_work.stat().st_mode) == 0o2770
    for relative_path in (".config", ".cache", ".local/state", "tmp"):
        assert stat.S_IMODE((session_home / relative_path).stat().st_mode) == 0o700


@pytest.mark.anyio
async def test_agent_nsjail_separates_claude_and_tool_uids(tmp_path: Path) -> None:
    """Exercise real UID maps, capability dropping, and private-home access."""
    nsjail_path = Path(nsjail_module.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(nsjail_module.TRACECAT__SANDBOX_ROOTFS_PATH)
    if sys.platform != "linux" or not nsjail_path.exists() or not rootfs_path.exists():
        pytest.skip("real agent nsjail isolation requires the Linux executor image")

    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    llm_socket_path = socket_dir / "llm.sock"
    mcp_socket_path = socket_dir / "mcp.sock"
    llm_socket_path.touch()
    mcp_socket_path.touch()
    session_home = tmp_path / "agent-home"
    session_work = tmp_path / "agent-work"
    job_dir = tmp_path / "job"
    init_payload_path = tmp_path / "init.json"
    claude_script = (
        f"TOOL_SCRIPT = {textwrap.dedent(_TOOL_IDENTITY_PROBE)!r}\n"
        f"{textwrap.dedent(_CLAUDE_IDENTITY_PROBE)}"
    )
    init_payload_path.write_text(
        json.dumps(
            {
                "command": ["/usr/local/bin/python3", "-c", claude_script],
                "env": {},
                "cwd": "/work",
                "mcp_bridge_port": 4101,
            }
        )
    )

    spawned = await nsjail_module.spawn_jailed_runtime(
        socket_dir=socket_dir,
        init_payload_path=init_payload_path,
        llm_socket_path=llm_socket_path,
        mcp_socket_path=mcp_socket_path,
        control_socket_required=False,
        pipe_stdin=True,
        job_dir=job_dir,
        session_home_dir=session_home,
        session_work_dir=session_work,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        spawned.process.communicate(), timeout=30
    )

    assert spawned.process.returncode == 0, stderr_bytes.decode(errors="replace")
    output_lines = stdout_bytes.decode().splitlines()
    assert output_lines
    result = json.loads(output_lines[-1])
    expected_claude_caps = f"{1 << 7:016x}"
    assert result["uid"] == AGENT_CLAUDE_UID
    assert result["euid"] == AGENT_CLAUDE_UID
    assert set(result["caps"].values()) == {expected_claude_caps}
    assert result["no_new_privs"] == "1"
    assert result["tool_returncode"] == 0, result["tool_stderr"]
    assert result["tool_home_denied"] is True
    assert result["read_work"] == "tool-shared"
    assert all(result["private_unchanged"].values())
    assert result["stolen_exists"] is False

    tool_result = result["tool"]
    assert tool_result["uid"] == AGENT_TOOL_UID
    assert tool_result["euid"] == AGENT_TOOL_UID
    assert tool_result["user"] == "tools"
    assert set(tool_result["caps"].values()) == {"0000000000000000"}
    assert tool_result["no_new_privs"] == "1"
    assert tool_result["home"] == "/home/tools"
    assert tool_result["tmpdir"] == "/home/tools/tmp"
    assert tool_result["read_work"] == "claude-shared"
    assert all(tool_result["private_denials"].values())


class _FakeProcess:
    pass


@pytest.mark.anyio
async def test_spawned_claude_shim_uses_explicit_stdio_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(nsjail_module, "TRACECAT__DISABLE_NSJAIL", True)
    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(
        *_args: object,
        **kwargs: object,
    ) -> _FakeProcess:
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        nsjail_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()

    await nsjail_module.spawn_jailed_runtime(
        socket_dir=socket_dir,
        init_payload_path=tmp_path / "init.json",
        pipe_stdin=True,
    )

    assert captured["kwargs"]["limit"] == nsjail_module.CLAUDE_SHIM_STDIO_LIMIT_BYTES
