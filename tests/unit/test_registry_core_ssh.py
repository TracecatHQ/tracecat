"""Tests for registry SSH actions."""

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import paramiko
import pytest
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from tracecat_registry._internal import remote_exec
from tracecat_registry._internal.exceptions import TracecatException
from tracecat_registry.core import ssh as registry_ssh


@pytest.fixture(autouse=True)
def mock_target_resolution(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    sock = MagicMock()
    monkeypatch.setattr(
        registry_ssh, "resolve_remote_target", lambda host, port: "198.51.100.10"
    )
    create_connection = MagicMock(return_value=sock)
    monkeypatch.setattr(registry_ssh.socket, "create_connection", create_connection)
    return create_connection


@pytest.fixture
def mock_ssh_client() -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    stdin = MagicMock()
    stdout = MagicMock()
    stderr = MagicMock()
    stdout.read.return_value = b"hello\n"
    stderr.read.return_value = b""
    stdout.channel.recv_exit_status.return_value = 0
    client.exec_command.return_value = (stdin, stdout, stderr)
    return client


@pytest.mark.parametrize(
    ("private_key", "expected_type"),
    [
        (ed25519.Ed25519PrivateKey.generate(), paramiko.Ed25519Key),
        (
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            paramiko.RSAKey,
        ),
        (ec.generate_private_key(ec.SECP256R1()), paramiko.ECDSAKey),
    ],
)
def test_load_private_key_supports_modern_key_types(
    private_key: ed25519.Ed25519PrivateKey
    | rsa.RSAPrivateKey
    | ec.EllipticCurvePrivateKey,
    expected_type: type[paramiko.PKey],
) -> None:
    serialized_key = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.OpenSSH,
        encryption_algorithm=NoEncryption(),
    ).decode()

    assert isinstance(registry_ssh._load_private_key(serialized_key), expected_type)


def test_load_private_key_rejects_invalid_key() -> None:
    with pytest.raises(ValueError, match="Unsupported or invalid private key format"):
        registry_ssh._load_private_key("not-a-private-key")


def test_load_private_key_rejects_removed_dsa_keys() -> None:
    serialized_key = dsa.generate_private_key(key_size=1024).private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=NoEncryption(),
    )

    with pytest.raises(ValueError, match="Use an Ed25519, RSA, or ECDSA key"):
        registry_ssh._load_private_key(serialized_key.decode())


def test_execute_command_rejects_missing_host_key_when_checking_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_ssh_client: MagicMock,
) -> None:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(
        registry_ssh, "_load_private_key", lambda private_key: MagicMock()
    )
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)

    with pytest.raises(ValueError, match="host_public_key is required"):
        registry_ssh.execute_command(
            command="hostname",
            host="example.com",
            username="root",
        )

    mock_ssh_client.connect.assert_not_called()


def test_execute_command_uses_transient_policy_when_host_key_checking_disabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_ssh_client: MagicMock,
) -> None:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(
        registry_ssh, "_load_private_key", lambda private_key: MagicMock()
    )
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)

    with pytest.warns(RuntimeWarning, match="not persisted across action runs"):
        result = registry_ssh.execute_command(
            command="hostname",
            host="example.com",
            username="root",
            host_key_checking=False,
        )

    mock_ssh_client.load_host_keys.assert_not_called()
    policy = mock_ssh_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, registry_ssh._TransientMissingHostKeyPolicy)
    assert result == {"stdout": "hello\n", "stderr": "", "exit_status": 0}


def test_execute_command_uses_known_hosts_when_host_key_checking_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_ssh_client: MagicMock,
    tmp_path,
) -> None:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(
        registry_ssh, "_load_private_key", lambda private_key: MagicMock()
    )
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)
    monkeypatch.setattr(
        registry_ssh.tempfile,
        "NamedTemporaryFile",
        lambda **kwargs: open(tmp_path / "known_hosts", "w", encoding="utf-8"),
    )

    registry_ssh.execute_command(
        command="hostname",
        host="example.com",
        username="root",
        host_public_key="ssh-ed25519 AAAA",
    )

    mock_ssh_client.load_host_keys.assert_called_once_with(
        str(tmp_path / "known_hosts")
    )
    policy = mock_ssh_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.RejectPolicy)


@pytest.fixture
def ssh_client_with_result(
    monkeypatch: pytest.MonkeyPatch, mock_ssh_client: MagicMock
) -> Callable[[int, bytes, bytes], MagicMock]:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(
        registry_ssh, "_load_private_key", lambda private_key: MagicMock()
    )
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)

    def configure(exit_status: int, stdout: bytes, stderr: bytes) -> MagicMock:
        _, out, err = mock_ssh_client.exec_command.return_value
        out.read.return_value = stdout
        err.read.return_value = stderr
        out.channel.recv_exit_status.return_value = exit_status
        return mock_ssh_client

    return configure


def _run(**kwargs: Any) -> registry_ssh.SSHCommandResult:
    with pytest.warns(RuntimeWarning):
        return registry_ssh.execute_command(
            command="hostname",
            host="example.com",
            username="root",
            host_key_checking=False,
            **kwargs,
        )


def test_execute_command_returns_nonzero_exit_by_default(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
) -> None:
    ssh_client_with_result(2, b"", b"boom\n")

    result = _run()

    assert result == {"stdout": "", "stderr": "boom\n", "exit_status": 2}


def test_execute_command_raises_on_disallowed_exit_status(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
) -> None:
    ssh_client_with_result(2, b"", b"permission denied\n")

    with pytest.raises(TracecatException, match="exited with status 2") as exc_info:
        _run(raise_on_error=True)

    assert "permission denied" in str(exc_info.value)


def test_execute_command_accepts_allowed_exit_status(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
) -> None:
    ssh_client_with_result(1, b"no match\n", b"")

    result = _run(raise_on_error=True, allowed_exit_codes=[0, 1])

    assert result["exit_status"] == 1


def test_execute_command_raises_on_literal_error_pattern(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
) -> None:
    ssh_client_with_result(0, b"status: FAILED (a+)+$\n", b"")

    with pytest.raises(TracecatException, match="matched error pattern"):
        _run(raise_on_error=True, error_patterns=["(a+)+$"])


def test_execute_command_ignores_error_patterns_without_raise_on_error(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
) -> None:
    ssh_client_with_result(0, b"FAILED\n", b"")

    assert _run(error_patterns=["FAILED"])["stdout"] == "FAILED\n"


@pytest.mark.parametrize(
    "error_patterns",
    [[""], ["x" * 257], ["x"] * 21],
)
def test_execute_command_rejects_invalid_error_patterns(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
    error_patterns: list[str],
) -> None:
    client = ssh_client_with_result(0, b"", b"")

    with pytest.raises(ValueError, match="error_patterns"):
        registry_ssh.execute_command(
            command="hostname",
            host="example.com",
            username="root",
            raise_on_error=True,
            error_patterns=error_patterns,
        )

    client.connect.assert_not_called()


def test_execute_command_dials_validated_address(
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
    mock_target_resolution: MagicMock,
) -> None:
    client = ssh_client_with_result(0, b"", b"")

    _run(port=2222)

    mock_target_resolution.assert_called_once_with(
        ("198.51.100.10", 2222), timeout=30.0
    )
    connect_kwargs = client.connect.call_args.kwargs
    assert connect_kwargs["hostname"] == "example.com"
    assert connect_kwargs["sock"] is mock_target_resolution.return_value


def test_execute_command_rejects_denied_target(
    monkeypatch: pytest.MonkeyPatch,
    ssh_client_with_result: Callable[[int, bytes, bytes], MagicMock],
) -> None:
    client = ssh_client_with_result(0, b"", b"")
    monkeypatch.setattr(
        registry_ssh,
        "resolve_remote_target",
        remote_exec.resolve_remote_target,
    )

    with pytest.raises(remote_exec.RemoteTargetDenied):
        registry_ssh.execute_command(
            command="hostname",
            host="127.0.0.1",
            username="root",
            host_key_checking=False,
        )

    client.connect.assert_not_called()
