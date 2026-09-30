"""Exercise a built production image without database or provider credentials."""

import asyncio
import os
import platform
import stat
import subprocess
import sys
from pathlib import Path

from tracecat import config
from tracecat.sandbox.service import SandboxService

SCRIPT = """
import os
import ssl
import subprocess
from pathlib import Path

def main():
    assert os.getuid() == 1000
    assert not Path('/app/tracecat').exists()
    assert not os.access('/usr/local/bin/python3', os.W_OK)
    probe = Path('/tmp/image-smoke.txt')
    probe.write_text('sandbox-write-ok')
    assert probe.read_text() == 'sandbox-write-ok'
    assert ssl.create_default_context().get_ca_certs()
    subprocess.run(['/usr/local/bin/node', '--version'], check=True)
    subprocess.run(['/usr/bin/jq', '--version'], check=True)
    output = subprocess.check_output(
        ['/usr/local/bin/duckdb', '-csv', '-noheader', '-c', 'SELECT 42;'],
        text=True,
    )
    assert output.strip() == '42'
    return {'uid': os.getuid(), 'result': 42}
"""


def run_smoke(module: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    print(result.stdout, flush=True)
    print(result.stderr, file=sys.stderr, flush=True)
    result.check_returncode()
    assert "TRACE_CAT_EXECUTOR_ACTION_SMOKE_SKIP:" not in result.stdout
    print(f"PASS {module} {' '.join(args)}", flush=True)


async def main() -> None:
    assert os.getuid() == 1001, "Exercise the production apiuser"
    assert not config.TRACECAT__DISABLE_NSJAIL, "Real nsjail is required"
    assert Path(config.TRACECAT__SANDBOX_NSJAIL_PATH).is_file()
    assert Path("/dev/net/tun").exists()
    rootfs = Path(config.TRACECAT__SANDBOX_ROOTFS_PATH)
    assert rootfs.is_dir()
    for path in rootfs.rglob("*"):
        mode = path.lstat().st_mode
        assert not mode & (stat.S_ISUID | stat.S_ISGID), path
    if platform.machine() == "x86_64":
        loader = rootfs / "lib64/ld-linux-x86-64.so.2"
        assert loader.is_symlink(), loader
        print(f"AMD64 loader: {loader.readlink()}", flush=True)
    print("PASS rootfs has no setuid/setgid paths", flush=True)

    result = await SandboxService().run_python(SCRIPT, timeout_seconds=45)
    assert result == {"uid": 1000, "result": 42}, result
    print("PASS real run_python sandbox and bundled executables", flush=True)

    run_smoke("tests.smoke_executor_cgroup")
    for case in ("nsjail-current-builtin", "nsjail-squashfs"):
        run_smoke("tests.unit.test_executor_sandbox_nsjail", "--run-smoke", case)
    for flag in ("--run-nsjail-harness-smoke", "--run-nsjail-duckdb-smoke"):
        run_smoke("tests.unit.test_agent_sandbox_litellm", flag)


if __name__ == "__main__":
    asyncio.run(main())
