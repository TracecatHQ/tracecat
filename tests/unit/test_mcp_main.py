from __future__ import annotations

import pytest

from tracecat.mcp import __main__ as mcp_main


def test_main_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def _run_mcp_server() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("missing oidc config")

    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(mcp_main.time, "sleep", lambda _: None)

    mcp_main.main()

    assert len(attempts) == 3


def test_main_exits_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def _run_mcp_server() -> None:
        attempts.append(1)
        raise ValueError("missing oidc config")

    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(mcp_main.time, "sleep", lambda _: None)

    with pytest.raises(SystemExit, match="1"):
        mcp_main.main()

    assert len(attempts) == 3


def test_main_stops_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def _run_mcp_server() -> None:
        attempts.append(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_main, "_run_mcp_server", _run_mcp_server)
    monkeypatch.setattr(mcp_main, "TRACECAT_MCP__STARTUP_MAX_ATTEMPTS", 3)

    mcp_main.main()

    assert len(attempts) == 1
