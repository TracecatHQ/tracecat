"""Tests for registry SSH actions."""

from unittest.mock import MagicMock

import paramiko
import pytest
from tracecat_registry.core import ssh as registry_ssh


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


def test_execute_command_rejects_missing_host_key_when_checking_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_ssh_client: MagicMock,
) -> None:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(registry_ssh, "_load_private_key", lambda private_key: MagicMock())
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)

    with pytest.raises(ValueError, match="host_public_key is required"):
        registry_ssh.execute_command(
            command="hostname",
            host="example.com",
            username="root",
        )

    mock_ssh_client.connect.assert_not_called()


def test_execute_command_uses_auto_add_policy_when_host_key_checking_disabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_ssh_client: MagicMock,
) -> None:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(registry_ssh, "_load_private_key", lambda private_key: MagicMock())
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)

    with pytest.warns(RuntimeWarning, match="host_key_checking=False temporarily"):
        result = registry_ssh.execute_command(
            command="hostname",
            host="example.com",
            username="root",
            host_key_checking=False,
        )

    mock_ssh_client.load_host_keys.assert_not_called()
    policy = mock_ssh_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.AutoAddPolicy)
    assert result == {"stdout": "hello\n", "stderr": "", "exit_status": 0}


def test_execute_command_uses_known_hosts_when_host_key_checking_enabled(
    monkeypatch: pytest.MonkeyPatch,
    mock_ssh_client: MagicMock,
    tmp_path,
) -> None:
    monkeypatch.setattr(registry_ssh.secrets, "get", lambda key: "private-key")
    monkeypatch.setattr(registry_ssh, "_load_private_key", lambda private_key: MagicMock())
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_ssh_client)
    monkeypatch.setattr(registry_ssh.tempfile, "NamedTemporaryFile", lambda **kwargs: open(tmp_path / "known_hosts", "w", encoding="utf-8"))

    registry_ssh.execute_command(
        command="hostname",
        host="example.com",
        username="root",
        host_public_key="ssh-ed25519 AAAA",
    )

    mock_ssh_client.load_host_keys.assert_called_once_with(str(tmp_path / "known_hosts"))
    policy = mock_ssh_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.RejectPolicy)
