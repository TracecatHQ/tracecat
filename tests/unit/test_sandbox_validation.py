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
from tracecat.sandbox.types import SandboxConfig


class TestValidateEnvKey:
    """Tests for _validate_env_key function."""

    def test_valid_simple_key(self):
        """Simple alphanumeric keys should pass."""
        _validate_env_key("MY_VAR")
        _validate_env_key("API_KEY")
        _validate_env_key("SECRET123")

    def test_valid_underscore_prefix(self):
        """Keys starting with underscore should pass."""
        _validate_env_key("_private")
        _validate_env_key("_INTERNAL_VAR")

    def test_valid_single_letter(self):
        """Single letter keys should pass."""
        _validate_env_key("A")
        _validate_env_key("_")

    def test_valid_long_key(self):
        """Long keys should pass."""
        _validate_env_key("VERY_LONG_ENVIRONMENT_VARIABLE_NAME_123")

    def test_invalid_starts_with_number(self):
        """Keys starting with number should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_env_key("123VAR")
        assert "Invalid environment variable key" in str(exc_info.value)

    def test_invalid_hyphen(self):
        """Keys with hyphens should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_env_key("MY-VAR")
        assert "Invalid environment variable key" in str(exc_info.value)

    def test_invalid_newline(self):
        """Keys with newlines should fail (injection attempt)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_env_key("VAR\nINJECT")
        assert "Invalid environment variable key" in str(exc_info.value)

    def test_invalid_quote(self):
        """Keys with quotes should fail (injection attempt)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_env_key('VAR"INJECT')
        assert "Invalid environment variable key" in str(exc_info.value)

    def test_invalid_space(self):
        """Keys with spaces should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_env_key("MY VAR")
        assert "Invalid environment variable key" in str(exc_info.value)

    def test_invalid_empty(self):
        """Empty keys should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_env_key("")
        assert "Invalid environment variable key" in str(exc_info.value)

    def test_invalid_special_characters(self):
        """Keys with special characters should fail."""
        for char in ["=", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")"]:
            with pytest.raises(SandboxValidationError):
                _validate_env_key(f"VAR{char}NAME")


class TestValidatePath:
    """Tests for _validate_path function."""

    def test_valid_simple_path(self):
        """Simple paths should pass."""
        _validate_path(Path("/tmp/job-123"), "job_dir")
        _validate_path(Path("/var/lib/sandbox"), "rootfs")

    def test_valid_path_with_dots(self):
        """Paths with dots in filenames should pass."""
        _validate_path(Path("/tmp/job.123"), "job_dir")
        _validate_path(Path("/home/user/.config"), "config")

    def test_valid_absolute_path(self):
        """Absolute paths should pass."""
        _validate_path(Path("/usr/local/bin"), "bin_dir")

    def test_valid_relative_path(self):
        """Relative paths should pass."""
        _validate_path(Path("./local/cache"), "cache")
        _validate_path(Path("cache/packages"), "packages")

    def test_invalid_double_quote(self):
        """Paths with double quotes should fail (injection)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path('/tmp/job"inject'), "job_dir")
        assert "dangerous characters" in str(exc_info.value)

    def test_invalid_single_quote(self):
        """Paths with single quotes should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path("/tmp/job'inject"), "job_dir")
        assert "dangerous characters" in str(exc_info.value)

    def test_invalid_newline(self):
        """Paths with newlines should fail (injection)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path("/tmp/job\ninject"), "job_dir")
        assert "dangerous characters" in str(exc_info.value)

    def test_invalid_carriage_return(self):
        """Paths with carriage returns should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path("/tmp/job\rinject"), "job_dir")
        assert "dangerous characters" in str(exc_info.value)

    def test_invalid_backslash(self):
        """Paths with backslashes should fail (Windows-style)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path("/tmp/job\\inject"), "job_dir")
        assert "dangerous characters" in str(exc_info.value)

    def test_invalid_curly_braces(self):
        """Paths with curly braces should fail (protobuf syntax)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path("/tmp/job{inject}"), "job_dir")
        assert "dangerous characters" in str(exc_info.value)

    def test_error_includes_path_name(self):
        """Error message should include the path name for context."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_path(Path('/tmp/bad"path'), "my_special_path")
        assert "my_special_path" in str(exc_info.value)


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

        # Ensure NO TRACECAT__ vars are present
        tracecat_vars = [k for k in env_map.keys() if k.startswith("TRACECAT__")]
        assert tracecat_vars == [], f"Found TRACECAT__ vars in env_map: {tracecat_vars}"

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

        # Expected keys are ONLY base env + user-specified vars
        expected_keys = {
            "PATH",
            "HOME",
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONUNBUFFERED",
            "LANG",
            "LC_ALL",
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
        assert env_map["UV_CACHE_DIR"] == "/uv-cache"

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


class TestValidateCacheKey:
    """Tests for _validate_cache_key function."""

    def test_valid_short_hex(self):
        """Short hex strings should pass."""
        _validate_cache_key("abc123")
        _validate_cache_key("deadbeef")

    def test_valid_sha256_hex(self):
        """SHA256-length hex strings should pass."""
        _validate_cache_key("a" * 64)
        _validate_cache_key("0123456789abcdef" * 4)

    def test_valid_all_digits(self):
        """All-digit hex strings should pass."""
        _validate_cache_key("1234567890")

    def test_valid_all_letters(self):
        """All lowercase letter hex should pass."""
        _validate_cache_key("abcdef")

    def test_invalid_uppercase(self):
        """Uppercase hex should fail (we require lowercase)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("ABCDEF")
        assert "lowercase hex" in str(exc_info.value)

    def test_invalid_mixed_case(self):
        """Mixed case should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("AbCdEf")
        assert "lowercase hex" in str(exc_info.value)

    def test_invalid_non_hex_letters(self):
        """Non-hex letters should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("ghijkl")
        assert "Invalid cache_key" in str(exc_info.value)

    def test_invalid_special_characters(self):
        """Special characters should fail (injection attempt)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key('abc"123')
        assert "Invalid cache_key" in str(exc_info.value)

    def test_invalid_path_traversal(self):
        """Path traversal attempts should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("../../../etc/passwd")
        assert "Invalid cache_key" in str(exc_info.value)

    def test_invalid_empty(self):
        """Empty cache key should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("")
        assert "Invalid cache_key" in str(exc_info.value)

    def test_invalid_whitespace(self):
        """Whitespace should fail."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("abc 123")
        assert "Invalid cache_key" in str(exc_info.value)

    def test_invalid_newline(self):
        """Newlines should fail (injection attempt)."""
        with pytest.raises(SandboxValidationError) as exc_info:
            _validate_cache_key("abc\n123")
        assert "Invalid cache_key" in str(exc_info.value)
