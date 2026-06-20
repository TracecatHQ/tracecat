"""Integration tests for local-registry hot-reload wiring in docker-compose.yml.

These tests verify that the compose file's TRACECAT__LOCAL_REPOSITORY_* wiring
is consistent across all registry-aware services, so a bind-mounted custom
registry package is importable on every service and host edits are reflected
live without restart.

The tests mirror the real self-hosting flow documented at
https://docs.tracecat.com/self-hosting/docker-compose:

  1. Copy docker-compose.yml, Caddyfile, .env.example, env.sh into a temp dir
  2. Run env.sh non-interactively to generate .env
  3. Append TRACECAT__LOCAL_REPOSITORY_* settings to .env
  4. docker compose up -d
  5. Verify every service can import the bind-mounted package
  6. Verify host edits are visible without restart

Requirements:
  - Docker daemon reachable
  - Network access to pull ghcr.io/tracecathq/tracecat

Run with:
    uv run pytest tests/integration/test_local_registry_compose.py -x -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "tracecat-lr-test"
PUBLIC_APP_PORT = "18006"
TIMEOUT_BRINGUP = 600
TIMEOUT_EXEC = 120
POLL_INTERVAL = 5

REGISTRY_AWARE_SERVICES = [
    "api",
    "worker",
    "executor",
    "agent-worker",
    "litellm",
    "agent-executor",
    "mcp",
]

# Services to poll for "running" state during bringup. mcp is excluded because
# it depends on api:service_healthy, and api's startup (registry sync + tarball
# build) can exceed the Docker healthcheck timeout. mcp's compose wiring is
# verified via `docker compose run --rm --no-deps` in the import test instead.
POLL_SERVICES = [s for s in REGISTRY_AWARE_SERVICES if s != "mcp"]

pytestmark = [pytest.mark.integration, pytest.mark.slow]


# Override root conftest autouse fixtures that require local PostgreSQL, Redis,
# or MinIO. These compose-only tests interact with services inside Docker
# containers only — no local infrastructure is needed.


@pytest.fixture(autouse=True, scope="session")
def db():
    yield


@pytest.fixture(autouse=True, scope="session")
def default_org():
    yield


@pytest.fixture(autouse=True, scope="function")
def clean_redis_db():
    yield


@pytest.fixture(autouse=True, scope="function")
async def test_db_engine():
    yield


@pytest.fixture(autouse=True, scope="session")
def workflow_bucket():
    yield


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return result.returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not available",
)


@pytest.fixture(scope="module")
def local_registry_package(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Create a minimal Python package on the host for bind-mounting.

    Structure:
        <pkg_dir>/
            pyproject.toml
            test_actions/
                __init__.py
                udfs.py      # def add_numbers(a, b) -> a + b

    The directory is created under /tmp (not pytest's default tmp_path, which
    uses 0700 permissions) and chmod'd to 0755 so the container's apiuser
    (uid 1001) can traverse and read the bind-mounted files.
    """
    pkg_dir = Path(tempfile.mkdtemp(prefix="lr-pkg-"))
    (pkg_dir / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "test-actions"
            version = "0.1.0"
            """,
        )
    )
    actions_dir = pkg_dir / "test_actions"
    actions_dir.mkdir()
    (actions_dir / "__init__.py").write_text("")
    (actions_dir / "udfs.py").write_text(
        textwrap.dedent(
            '''\
            """Test UDFs for local-registry compose wiring verification."""


            def add_numbers(a: int, b: int) -> int:
                """Add two numbers."""
                return a + b
            ''',
        )
    )
    # Make world-readable so the container's apiuser (uid 1001) can access it
    os.chmod(pkg_dir, 0o755)
    os.chmod(actions_dir, 0o755)
    for f in pkg_dir.rglob("*"):
        if f.is_file():
            os.chmod(f, 0o644)
    return pkg_dir


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Self-hosting working directory with compose files copied from the repo."""
    wd = tmp_path_factory.mktemp("workdir")
    for name in ("docker-compose.yml", "Caddyfile", ".env.example", "env.sh"):
        src = REPO_ROOT / name
        if src.exists():
            shutil.copy2(src, wd / name)
    return wd


@pytest.fixture(scope="module")
def env_file(
    workdir: Path,
    local_registry_package: Path,
) -> Path:
    """Generate .env via env.sh (non-interactive) and append local-registry settings.

    Drives env.sh with stdin matching its interactive prompts:
      1. production mode?         -> n (development)
      2. PUBLIC_APP_URL?          -> localhost:<PUBLIC_APP_PORT>
      3. PostgreSQL SSL mode?     -> n
      4. superadmin email?        -> test@tracecat.com

    Then appends the two TRACECAT__LOCAL_REPOSITORY_* lines that a self-hoster
    would add to enable the local-registry feature.
    """
    stdin = f"n\nlocalhost:{PUBLIC_APP_PORT}\nn\ntest@tracecat.com\n"
    result = subprocess.run(
        ["bash", "env.sh"],
        input=stdin,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"env.sh failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    env_path = workdir / ".env"
    assert env_path.exists(), "env.sh did not create .env"

    with env_path.open("a") as f:
        f.write("\nTRACECAT__LOCAL_REPOSITORY_ENABLED=true\n")
        f.write(f"TRACECAT__LOCAL_REPOSITORY_PATH={local_registry_package}\n")

    return env_path


def _clean_compose_env() -> dict[str, str]:
    """Build a clean environment for Docker Compose subprocesses.

    The root conftest's env_sandbox fixture calls load_dotenv() which loads the
    repo root's .env into the test process environment. Those TRACECAT__*
    variables take precedence over workdir/.env when Docker Compose resolves
    variables, causing containers to connect to localhost instead of service
    names. This strips all TRACECAT__* and related vars so Compose reads them
    exclusively from workdir/.env.
    """
    env = os.environ.copy()
    for key in list(env.keys()):
        if (
            key.startswith("TRACECAT__")
            or key.startswith("TEMPORAL__")
            or key.startswith("NEXT_")
            or key
            in (
                "REDIS_URL",
                "MINIO_ROOT_USER",
                "MINIO_ROOT_PASSWORD",
                "PUBLIC_APP_URL",
                "PUBLIC_API_URL",
                "INTERNAL_API_URL",
                "BASE_DOMAIN",
                "ADDRESS",
                "NODE_ENV",
                "OAUTH_CLIENT_ID",
                "OAUTH_CLIENT_SECRET",
                "OIDC_ISSUER",
                "OIDC_CLIENT_ID",
                "OIDC_CLIENT_SECRET",
                "OIDC_SCOPES",
                "USER_AUTH_SECRET",
                "SAML_IDP_METADATA_URL",
                "LOG_LEVEL",
                "COMPOSE_PROJECT_NAME",
                "COMPOSE_BAKE",
            )
        ):
            del env[key]
    return env


@pytest.fixture(scope="module")
def compose_stack(
    workdir: Path,
    env_file: Path,
) -> tuple[Path, str]:
    """Bring up the compose stack and yield (workdir, project_name).

    Teardown runs `down --volumes --remove-orphans` in a finally block that
    wraps the entire bringup, so containers are always cleaned up even if
    startup fails.
    """
    try:
        # Bring up infrastructure first, wait for health, run migrations
        # manually, then start the rest with --no-deps. This avoids a race
        # where `docker compose up -d` starts migrations before the database
        # is fully ready (the healthcheck can pass before alembic can connect).
        infra = ["postgres_db", "redis", "minio", "temporal", "temporal_postgres_db"]
        subprocess.run(
            ["docker", "compose", "-p", PROJECT_NAME, "up", "-d"] + infra,
            cwd=workdir,
            env=_clean_compose_env(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_BRINGUP,
            check=False,
        )

        # Wait for infrastructure to be healthy
        infra_target = set(infra)
        deadline = time.time() + 120
        while time.time() < deadline:
            ps = subprocess.run(
                ["docker", "compose", "-p", PROJECT_NAME, "ps", "--format", "json"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if ps.returncode == 0:
                try:
                    svcs = [
                        json.loads(line)
                        for line in ps.stdout.strip().splitlines()
                        if line
                    ]
                except json.JSONDecodeError:
                    svcs = []
                healthy = {
                    s.get("Service")
                    for s in svcs
                    if s.get("State") in ("running", "healthy")
                }
                if infra_target.issubset(healthy):
                    break
            time.sleep(POLL_INTERVAL)

        # Run migrations manually (avoids the up -d race condition)
        mig_result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                PROJECT_NAME,
                "run",
                "--rm",
                "--no-deps",
                "migrations",
            ],
            cwd=workdir,
            env=_clean_compose_env(),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if mig_result.returncode != 0:
            pytest.fail(
                f"Migrations failed (exit {mig_result.returncode}):\n"
                f"stdout:\n{mig_result.stdout}\nstderr:\n{mig_result.stderr}"
            )

        # Start the rest of the stack (skip infra + migrations, use --no-deps
        # so compose doesn't try to re-verify migration completion)
        app_services = [
            "api",
            "worker",
            "executor",
            "agent-worker",
            "litellm",
            "agent-executor",
            "mcp",
            "ui",
            "caddy",
            "temporal_ui",
        ]
        subprocess.run(
            ["docker", "compose", "-p", PROJECT_NAME, "up", "-d", "--no-deps"]
            + app_services,
            cwd=workdir,
            env=_clean_compose_env(),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_BRINGUP,
            check=False,
        )

        target = set(POLL_SERVICES)
        deadline = time.time() + TIMEOUT_BRINGUP
        ready = False
        while time.time() < deadline:
            ps = subprocess.run(
                ["docker", "compose", "-p", PROJECT_NAME, "ps", "--format", "json"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if ps.returncode == 0:
                try:
                    services = [
                        json.loads(line)
                        for line in ps.stdout.strip().splitlines()
                        if line
                    ]
                except json.JSONDecodeError:
                    services = []
                running = {
                    s.get("Service")
                    for s in services
                    if s.get("State") in ("running", "healthy")
                }
                if target.issubset(running):
                    ready = True
                    break
            time.sleep(POLL_INTERVAL)

        if not ready:
            ps = subprocess.run(
                ["docker", "compose", "-p", PROJECT_NAME, "ps"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            pytest.fail(
                f"Services did not become ready within {TIMEOUT_BRINGUP}s:\n{ps.stdout}"
            )

        yield workdir, PROJECT_NAME
    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                PROJECT_NAME,
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def _compose_exec(
    workdir: Path,
    project: str,
    service: str,
    command: list[str],
    timeout: int = TIMEOUT_EXEC,
) -> subprocess.CompletedProcess[str]:
    """Run `docker compose exec -T <service> <command>` and return the result."""
    return subprocess.run(
        ["docker", "compose", "-p", project, "exec", "-T", service] + command,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _compose_run(
    workdir: Path,
    project: str,
    service: str,
    command: list[str],
    timeout: int = TIMEOUT_EXEC,
) -> subprocess.CompletedProcess[str]:
    """Run `docker compose run --rm --no-deps <service> <command>` and return the result.

    Used for services that may not be running (e.g. mcp, which depends on
    api:service_healthy). This creates a one-off container with the same
    compose config (volumes, env, PYTHONPATH) but no dependency on other
    services being healthy.
    """
    return subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            service,
        ]
        + command,
        cwd=workdir,
        env=_clean_compose_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@requires_docker
def test_config_render_wiring(
    env_file: Path,
    local_registry_package: Path,
) -> None:
    """Verify docker-compose.yml config renders correct local-registry wiring.

    Checks that every registry-aware service has the bind mount, env vars, and
    PYTHONPATH when the feature is enabled.
    """
    workdir = env_file.parent
    compose_yml = workdir / "docker-compose.yml"

    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_yml), "config"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"compose config failed: {result.stderr}"

    config = yaml.safe_load(result.stdout)

    for svc in REGISTRY_AWARE_SERVICES:
        svc_config = config["services"][svc]

        mounts = svc_config.get("volumes", [])
        has_mount = any(
            (isinstance(v, dict) and v.get("target") == "/app/local_registry")
            or (isinstance(v, str) and v.endswith(":/app/local_registry"))
            for v in mounts
        )
        assert has_mount, f"{svc}: /app/local_registry mount missing"

        env = svc_config.get("environment", {})
        assert "TRACECAT__LOCAL_REPOSITORY_PATH" in env, (
            f"{svc}: TRACECAT__LOCAL_REPOSITORY_PATH env missing"
        )
        assert "TRACECAT__LOCAL_REPOSITORY_ENABLED" in env, (
            f"{svc}: TRACECAT__LOCAL_REPOSITORY_ENABLED env missing"
        )

        pythonpath = env.get("PYTHONPATH", "")
        assert "/app/local_registry" in pythonpath, (
            f"{svc}: PYTHONPATH missing /app/local_registry (got: {pythonpath})"
        )

        if svc == "litellm":
            assert "/app/packages/tracecat-registry" in pythonpath, (
                f"litellm: PYTHONPATH lost package prefix (got: {pythonpath})"
            )
            assert "/app/packages/tracecat-ee" in pythonpath, (
                f"litellm: PYTHONPATH lost ee prefix (got: {pythonpath})"
            )


@requires_docker
def test_all_services_import_bind_mounted_package(
    compose_stack: tuple[Path, str],
) -> None:
    """Every registry-aware service can import the bind-mounted package.

    The 4 newly-wired services (worker, agent-worker, litellm, mcp) would fail
    pre-fix (no mount, no PYTHONPATH, or env-without-mount contradiction).
    """
    workdir, project = compose_stack

    import_snippet = (
        "import sys, test_actions.udfs as m; "
        "assert m.add_numbers(1, 2) == 3; "
        "assert any('local_registry' in p for p in sys.path), sys.path; "
        "print('ok')"
    )

    for svc in REGISTRY_AWARE_SERVICES:
        # mcp depends on api:service_healthy, which may not be reached if
        # api's startup (registry sync + tarball build) exceeds the Docker
        # healthcheck timeout. Use `run --rm --no-deps` to test mcp's compose
        # wiring without requiring the full dependency chain to be healthy.
        if svc == "mcp":
            result = _compose_run(
                workdir, project, svc, ["python", "-c", import_snippet]
            )
        else:
            result = _compose_exec(
                workdir, project, svc, ["python", "-c", import_snippet]
            )
        assert result.returncode == 0, (
            f"{svc}: import failed (exit {result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "ok" in result.stdout, f"{svc}: unexpected output: {result.stdout}"


@requires_docker
def test_body_edit_reflects_without_restart(
    compose_stack: tuple[Path, str],
    local_registry_package: Path,
) -> None:
    """Host file edits are visible inside the executor on the next importlib.reload.

    This verifies the bind mount is live (not a copied snapshot) and that
    importlib.reload picks up host edits -- the mechanism tracecat's
    registry/loaders.py uses in direct mode.
    """
    workdir, project = compose_stack
    udfs = local_registry_package / "test_actions" / "udfs.py"

    baseline = _compose_exec(
        workdir,
        project,
        "executor",
        [
            "python",
            "-c",
            "import test_actions.udfs as m; "
            "assert m.add_numbers(1, 2) == 3; "
            "print('baseline ok')",
        ],
    )
    assert baseline.returncode == 0, (
        f"baseline import failed:\nstdout: {baseline.stdout}\nstderr: {baseline.stderr}"
    )
    assert "baseline ok" in baseline.stdout

    udfs.write_text(
        textwrap.dedent(
            '''\
            """Test UDFs for local-registry compose wiring verification."""


            def add_numbers(a: int, b: int) -> int:
                """Add two numbers and add 1."""
                return a + b + 1
            ''',
        )
    )

    reload = _compose_exec(
        workdir,
        project,
        "executor",
        [
            "python",
            "-c",
            "import importlib, test_actions.udfs as m; "
            "importlib.reload(m); "
            "assert m.add_numbers(1, 2) == 4, f'got {m.add_numbers(1, 2)}'; "
            "print('reload ok')",
        ],
    )
    assert reload.returncode == 0, (
        f"reload failed:\nstdout: {reload.stdout}\nstderr: {reload.stderr}"
    )
    assert "reload ok" in reload.stdout
