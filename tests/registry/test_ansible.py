from __future__ import annotations

from typing import Any

import tracecat_registry.integrations.ansible as ansible_integration


class _FakeRunner:
    def __init__(self) -> None:
        self.stdout = ""
        self.events = []


def test_run_playbook_sets_quiet_by_default(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(ansible_integration, "Runner", _FakeRunner)
    monkeypatch.setattr(ansible_integration.secrets, "get", lambda _key: None)

    def fake_run(**kwargs):
        captured.update(kwargs)
        return _FakeRunner()

    monkeypatch.setattr(ansible_integration, "run", fake_run)

    ansible_integration.run_playbook(
        playbook=[{"name": "Smoke test", "hosts": "all", "tasks": []}],
        host="192.168.1.10",
        host_name="test-host",
        user="ubuntu",
    )

    assert captured["quiet"] is True


def test_run_playbook_allows_quiet_override(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(ansible_integration, "Runner", _FakeRunner)
    monkeypatch.setattr(ansible_integration.secrets, "get", lambda _key: None)

    def fake_run(**kwargs):
        captured.update(kwargs)
        return _FakeRunner()

    monkeypatch.setattr(ansible_integration, "run", fake_run)

    ansible_integration.run_playbook(
        playbook=[{"name": "Smoke test", "hosts": "all", "tasks": []}],
        host="192.168.1.10",
        host_name="test-host",
        user="ubuntu",
        runner_kwargs={"quiet": False},
    )

    assert captured["quiet"] is False
