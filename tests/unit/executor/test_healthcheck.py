from __future__ import annotations

from pathlib import Path

from tracecat.executor import healthcheck


def test_healthcheck_ready(monkeypatch) -> None:
    monkeypatch.setattr(healthcheck, "is_warm_ready", lambda: True)

    assert healthcheck.main() == 0


def test_healthcheck_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(healthcheck, "is_warm_ready", lambda: False)
    monkeypatch.setattr(
        healthcheck,
        "get_warm_ready_file",
        lambda: Path("/tmp/tracecat/executor-warm.ready"),
    )

    assert healthcheck.main() == 1
