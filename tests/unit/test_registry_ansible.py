"""Tests for the Ansible playbook integration guards and failure handling."""

import io
from typing import Any
from unittest.mock import MagicMock

import pytest
from ansible_runner import Runner
from tracecat_registry._internal import remote_exec
from tracecat_registry._internal.exceptions import TracecatException
from tracecat_registry.integrations import ansible

PLAYBOOK = [{"hosts": "all", "tasks": [{"command": "true"}]}]


def _runner(
    status: str = "successful",
    rc: int = 0,
    stdout: str = "",
    events: list[dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
) -> MagicMock:
    runner = MagicMock(spec=Runner)
    runner.status = status
    runner.rc = rc
    runner.stdout = io.StringIO(stdout)
    runner.events = iter(events or [])
    runner.stats = stats if stats is not None else {"processed": {"server": 1}}
    return runner


@pytest.fixture
def mock_run(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setattr(ansible.secrets, "get", lambda key: None)
    monkeypatch.setattr(
        ansible, "resolve_remote_target", lambda host, port: "198.51.100.20"
    )
    run = MagicMock(return_value=_runner())
    monkeypatch.setattr(ansible, "run", run)
    return run


def _run_playbook(**kwargs: Any) -> list[dict[str, Any]] | list[str]:
    params: dict[str, Any] = {
        "playbook": PLAYBOOK,
        "host": "server.example.com",
        "host_name": "server",
        "user": "deploy",
    }
    params.update(kwargs)
    return ansible.run_playbook(**params)


def test_pins_inventory_to_validated_address_and_limits_host(
    mock_run: MagicMock,
) -> None:
    _run_playbook()

    kwargs = mock_run.call_args.kwargs
    host_vars = kwargs["inventory"]["all"]["hosts"]["server"]
    assert host_vars["ansible_host"] == "198.51.100.20"
    assert host_vars["ansible_connection"] == "ssh"
    assert kwargs["limit"] == "server"
    assert kwargs["quiet"] is True


def test_returns_failed_events_by_default(mock_run: MagicMock) -> None:
    events = [{"event": "runner_on_failed", "stdout": "fatal"}]
    mock_run.return_value = _runner(status="failed", rc=2, events=events)

    assert _run_playbook() == events


@pytest.mark.parametrize(
    "status,rc",
    [("failed", 2), ("timeout", 254), ("canceled", 254)],
)
def test_raises_on_unsuccessful_status(
    mock_run: MagicMock, status: str, rc: int
) -> None:
    mock_run.return_value = _runner(
        status=status,
        rc=rc,
        stats={"failures": {"server": 1}, "dark": {}},
        events=[
            {
                "event": "runner_on_failed",
                "event_data": {"task": "check", "res": {"msg": "non-zero return"}},
            }
        ],
    )

    with pytest.raises(TracecatException, match=f"status '{status}'") as exc_info:
        _run_playbook(raise_on_failure=True)

    message = str(exc_info.value)
    assert "Failed hosts: 1" in message
    assert "check: non-zero return" in message


def test_raises_on_unreachable_host(mock_run: MagicMock) -> None:
    mock_run.return_value = _runner(
        status="failed",
        rc=4,
        stats={"failures": {}, "dark": {"server": 1}},
    )

    with pytest.raises(TracecatException, match="Unreachable hosts: 1"):
        _run_playbook(raise_on_failure=True)


def test_raises_when_no_hosts_matched(mock_run: MagicMock) -> None:
    mock_run.return_value = _runner(stats={"processed": {}})

    with pytest.raises(TracecatException, match="Use `hosts: all`"):
        _run_playbook(raise_on_failure=True)


def test_raises_on_literal_error_pattern(mock_run: MagicMock) -> None:
    mock_run.return_value = _runner(stdout="TASK [x]\nWARNING: drift detected\n")

    with pytest.raises(TracecatException, match="matched error pattern"):
        _run_playbook(raise_on_failure=True, error_patterns=["drift detected"])


def test_successful_run_does_not_raise(mock_run: MagicMock) -> None:
    events = [{"event": "playbook_on_stats"}]
    mock_run.return_value = _runner(stdout="ok", events=events)

    assert _run_playbook(raise_on_failure=True, error_patterns=["FAILED"]) == events


def test_passes_runner_kwargs(mock_run: MagicMock) -> None:
    _run_playbook(runner_kwargs={"verbosity": 2, "tags": "deploy"})

    kwargs = mock_run.call_args.kwargs
    assert kwargs["verbosity"] == 2
    assert kwargs["tags"] == "deploy"


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "localhost"])
def test_rejects_denied_target(
    monkeypatch: pytest.MonkeyPatch, mock_run: MagicMock, host: str
) -> None:
    monkeypatch.setattr(
        ansible, "resolve_remote_target", remote_exec.resolve_remote_target
    )

    with pytest.raises(remote_exec.RemoteTargetDenied):
        _run_playbook(host=host)

    mock_run.assert_not_called()
