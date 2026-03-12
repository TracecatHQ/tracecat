from __future__ import annotations

from tracecat import config
from tracecat.executor.warm_readiness import (
    clear_warm_ready_file,
    get_warm_ready_file,
    is_warm_ready,
    mark_warm_ready,
)


def test_mark_warm_ready_creates_marker(tmp_path, monkeypatch) -> None:
    ready_file = tmp_path / "executor-warm.ready"
    monkeypatch.setattr(
        config,
        "TRACECAT__EXECUTOR_WARM_READY_FILE",
        str(ready_file),
    )

    mark_warm_ready()

    assert get_warm_ready_file() == ready_file
    assert ready_file.exists()
    assert is_warm_ready() is True


def test_clear_warm_ready_file_removes_marker(tmp_path, monkeypatch) -> None:
    ready_file = tmp_path / "executor-warm.ready"
    monkeypatch.setattr(
        config,
        "TRACECAT__EXECUTOR_WARM_READY_FILE",
        str(ready_file),
    )
    mark_warm_ready()

    clear_warm_ready_file()

    assert ready_file.exists() is False
    assert is_warm_ready() is False

    # Missing files should be a no-op
    clear_warm_ready_file()
