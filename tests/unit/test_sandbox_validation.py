"""Tests for nsjail sandbox input validation functions."""

from pathlib import Path

import pytest

from tracecat.sandbox.exceptions import SandboxValidationError
from tracecat.sandbox.executor import (
    NsjailExecutor,
    _validate_cache_key,
    _validate_env_key,
    _validate_path,
)
from tracecat.sandbox.types import (
    ResourceLimits,
    SandboxBindMount,
    SandboxConfig,
)
from tracecat.sandbox.wrapper import INSTALL_SCRIPT


@pytest.mark.parametrize(
    ("key", "valid"),
    [
        ("MY_VAR", True),
        ("API_KEY", True),
        ("SECRET123", True),
        ("_private", True),
        ("_INTERNAL_VAR", True),
        ("A", True),
        ("_", True),
        ("VERY_LONG_ENVIRONMENT_VARIABLE_NAME_123", True),
        ("123VAR", False),
        ("MY-VAR", False),
        ("VAR\nINJECT", False),
        ('VAR"INJECT', False),
        ("MY VAR", False),
        ("", False),
        *[
            (f"VAR{char}NAME", False)
            for char in ["=", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")"]
        ],
    ],
)
def test_validate_env_key(key: str, valid: bool) -> None:
    if valid:
        _validate_env_key(key)
    else:
        with pytest.raises(
            SandboxValidationError, match="Invalid environment variable key"
        ):
            _validate_env_key(key)


@pytest.mark.parametrize(
    ("path", "valid"),
    [
        ("/tmp/job-123", True),
        ("/var/lib/sandbox", True),
        ("/tmp/job.123", True),
        ("/home/user/.config", True),
        ("/usr/local/bin", True),
        ("./local/cache", True),
        ("cache/packages", True),
        ('/tmp/job"inject', False),
        ("/tmp/job'inject", False),
        ("/tmp/job\ninject", False),
        ("/tmp/job\rinject", False),
        ("/tmp/job\\inject", False),
        ("/tmp/job{inject}", False),
    ],
)
def test_validate_path(path: str, valid: bool) -> None:
    if valid:
        _validate_path(Path(path), "job_dir")
    else:
        with pytest.raises(SandboxValidationError, match="dangerous characters"):
            _validate_path(Path(path), "job_dir")


def test_validate_path_error_includes_path_name() -> None:
    with pytest.raises(SandboxValidationError) as exc_info:
        _validate_path(Path('/tmp/bad"path'), "my_special_path")
    assert "my_special_path" in str(exc_info.value)


class TestBindMountValidation:
    """Tests for explicit phase-scoped sandbox mounts."""

    def test_bind_mount_destination_must_be_absolute(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.touch()

        with pytest.raises(SandboxValidationError, match="must be absolute"):
            NsjailExecutor()._build_config(
                job_dir=tmp_path / "job",
                phase="execute",
                config=SandboxConfig(
                    bind_mounts=[
                        SandboxBindMount(
                            source=source,
                            destination=Path("relative/path"),
                        )
                    ]
                ),
            )

    def test_bind_mount_source_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(SandboxValidationError, match="source does not exist"):
            NsjailExecutor()._build_config(
                job_dir=tmp_path / "job",
                phase="execute",
                config=SandboxConfig(
                    bind_mounts=[
                        SandboxBindMount(
                            source=tmp_path / "missing",
                            destination=Path("/run/capability"),
                        )
                    ]
                ),
            )


class TestEnvMap:
    """Tests for sanitized environment construction."""

    def test_env_map_does_not_inherit_host_env(self, monkeypatch: pytest.MonkeyPatch):
        """Only explicit env_vars and base env should be present."""
        monkeypatch.setenv("TRACECAT__DB_URI", "secret")
        executor = NsjailExecutor()
        config = SandboxConfig(env_vars={"USER_KEY": "value"})

        env_map = executor._build_env_map(config, "execute")

        assert env_map["USER_KEY"] == "value"
        assert "TRACECAT__DB_URI" not in env_map
        assert env_map["PATH"] == "/usr/local/bin:/usr/bin:/bin"

    def test_no_tracecat_vars_leak(self, monkeypatch: pytest.MonkeyPatch):
        """Verify NO TRACECAT__ prefixed vars are included in env_map.

        This is a comprehensive check to ensure no secrets configured via
        TRACECAT__ environment variables leak into the sandbox.
        """
        # Set multiple TRACECAT__ vars to simulate real environment
        monkeypatch.setenv("TRACECAT__DB_URI", "postgresql://secret:secret@db/tracecat")
        monkeypatch.setenv("TRACECAT__API_KEY", "sk-secret-key-12345")
        monkeypatch.setenv("TRACECAT__SERVICE_KEY", "service-secret")
        monkeypatch.setenv("TRACECAT__SIGNING_SECRET", "hmac-signing-secret")
        monkeypatch.setenv("TRACECAT__ENCRYPTION_KEY", "encryption-key-secret")

        executor = NsjailExecutor()
        config = SandboxConfig(env_vars={"USER_VAR": "safe-value"})

        env_map = executor._build_env_map(config, "execute")

        # Ensure only the host-injected process-cap var is present: no
        # secrets leak. TRACECAT__SANDBOX_RLIMIT_NPROC is injected by the
        # executor itself (not read from the host environment) so the jailed
        # wrapper can enforce the process cap that nsjail cannot.
        tracecat_vars = [k for k in env_map.keys() if k.startswith("TRACECAT__")]
        assert tracecat_vars == ["TRACECAT__SANDBOX_RLIMIT_NPROC"], (
            f"Unexpected TRACECAT__ vars in env_map: {tracecat_vars}"
        )

    def test_env_map_only_contains_expected_keys(self, monkeypatch: pytest.MonkeyPatch):
        """Verify env_map contains ONLY the expected keys, nothing more."""
        # Set various host env vars that should NOT leak
        monkeypatch.setenv("TRACECAT__DB_URI", "secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
        monkeypatch.setenv("DATABASE_PASSWORD", "db-pass")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")

        executor = NsjailExecutor()
        config = SandboxConfig(env_vars={"USER_VAR": "value"})

        env_map = executor._build_env_map(config, "execute")

        # Expected keys are ONLY base env + user-specified vars + the
        # host-injected process cap
        expected_keys = {
            "PATH",
            "HOME",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUNBUFFERED",
            "LANG",
            "LC_ALL",
            "TRACECAT__SANDBOX_RLIMIT_NPROC",
            "USER_VAR",
        }
        assert set(env_map.keys()) == expected_keys

    def test_env_map_with_cache_key_adds_pythonpath(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Verify PYTHONPATH is added when cache_key is provided and path exists."""
        # Create a mock cache directory structure
        cache_dir = tmp_path / "packages" / "abcdef123456" / "site-packages"
        cache_dir.mkdir(parents=True)

        executor = NsjailExecutor(cache_dir=str(tmp_path))
        config = SandboxConfig(env_vars={})

        env_map = executor._build_env_map(config, "execute", cache_key="abcdef123456")

        # PYTHONPATH should be added when cache exists
        assert "PYTHONPATH" in env_map
        assert env_map["PYTHONPATH"] == "/packages"

    def test_env_map_install_phase_has_uv_cache(self):
        """Verify install phase includes UV_CACHE_DIR."""
        executor = NsjailExecutor()
        config = SandboxConfig()

        env_map = executor._build_env_map(config, "install")

        assert "UV_CACHE_DIR" in env_map
        assert env_map["UV_CACHE_DIR"] == "/cache/uv-cache"

    def test_env_map_install_phase_injects_nproc_limit(self):
        """Install must inject the process cap: uv runs untrusted build backends."""
        executor = NsjailExecutor()
        config = SandboxConfig(resources=ResourceLimits(max_processes=32))

        env_map = executor._build_env_map(config, "install")

        assert env_map["TRACECAT__SANDBOX_RLIMIT_NPROC"] == "32"


class TestInstallScript:
    """Tests for the static INSTALL_SCRIPT entrypoint."""

    def test_install_script_is_valid_python(self):
        compile(INSTALL_SCRIPT, "<INSTALL_SCRIPT>", "exec")

    def test_install_script_enforces_nproc_before_uv(self):
        """The install entrypoint must apply the process cap before uv runs.

        uv executes arbitrary build backends from user-selected source
        dependencies; without the cap, a fork bomb in a build backend can
        exhaust the shared container PID limit and disrupt other jails.
        """
        assert "_enforce_nproc_limit()" in INSTALL_SCRIPT
        # rindex: the call site (not the def line) must precede subprocess.run.
        enforcement_pos = INSTALL_SCRIPT.rindex("_enforce_nproc_limit()")
        uv_pos = INSTALL_SCRIPT.index("subprocess.run")
        assert enforcement_pos < uv_pos, (
            "Process cap must be applied before launching uv"
        )

    def test_no_sensitive_host_vars_leak(self, monkeypatch: pytest.MonkeyPatch):
        """Comprehensive test that common sensitive env vars don't leak.

        This tests patterns commonly used for secrets in production environments.
        """
        sensitive_vars = {
            # Cloud provider secrets
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "AWS_SESSION_TOKEN": "aws-token",
            "AZURE_CLIENT_SECRET": "azure-secret",
            "GCP_SERVICE_ACCOUNT_KEY": "gcp-key",
            # Database credentials
            "DATABASE_URL": "postgres://user:pass@host/db",
            "REDIS_PASSWORD": "redis-pass",
            "MONGODB_URI": "mongodb://user:pass@host",
            # API keys
            "OPENAI_API_KEY": "sk-openai-key",
            "ANTHROPIC_API_KEY": "sk-anthropic-key",
            "GITHUB_TOKEN": "ghp_token",
            "SLACK_BOT_TOKEN": "xoxb-token",
            # Generic secrets
            "SECRET_KEY": "secret",
            "JWT_SECRET": "jwt-secret",
            "ENCRYPTION_KEY": "enc-key",
            # Docker/K8s
            "DOCKER_PASSWORD": "docker-pass",
            "KUBECONFIG": "/path/to/kubeconfig",
        }

        for key, value in sensitive_vars.items():
            monkeypatch.setenv(key, value)

        executor = NsjailExecutor()
        config = SandboxConfig(env_vars={"SAFE_USER_VAR": "user-value"})

        env_map = executor._build_env_map(config, "execute")

        # Verify NONE of the sensitive vars are present
        for key in sensitive_vars:
            assert key not in env_map, f"Sensitive var {key} leaked into sandbox"

        # Verify user var is present
        assert env_map["SAFE_USER_VAR"] == "user-value"


@pytest.mark.parametrize(
    ("cache_key", "error"),
    [
        ("abc123", None),
        ("deadbeef", None),
        ("a" * 64, None),
        ("0123456789abcdef" * 4, None),
        ("1234567890", None),
        ("abcdef", None),
        ("ABCDEF", "lowercase hex"),
        ("AbCdEf", "lowercase hex"),
        ("ghijkl", "Invalid cache_key"),
        ('abc"123', "Invalid cache_key"),
        ("../../../etc/passwd", "Invalid cache_key"),
        ("", "Invalid cache_key"),
        ("abc 123", "Invalid cache_key"),
        ("abc\n123", "Invalid cache_key"),
    ],
)
def test_validate_cache_key(cache_key: str, error: str | None) -> None:
    if error is None:
        _validate_cache_key(cache_key)
    else:
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key(cache_key)
        assert error in str(exc_info.value)
