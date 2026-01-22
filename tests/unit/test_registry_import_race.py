"""
Reproduction harness for the historical import/reload race around `tracecat_registry`.

This file contains two tests:

1) test_import_reload_race_old_behavior
   - Monkeypatches `import_and_reload` to the old, unsafe strategy
     (removing the package from `sys.modules` and sleeping) to widen
     the race window, then drives concurrent imports and reloads.
   - This test should reliably produce intermittent ModuleNotFoundError/KeyError
     on affected environments, demonstrating the race.

2) test_import_reload_no_race_with_lock
   - Uses the current, safe implementation of `import_and_reload`
     (process‑wide lock, no pop from sys.modules) and drives the same
     concurrent pattern. It asserts that no import errors occur.

These tests are intentionally lightweight and self‑contained.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

from tracecat.registry.repository import import_and_reload as safe_import_and_reload

pytestmark = pytest.mark.regression


def _bad_import_and_reload(module_name: str) -> ModuleType:
    """Old, unsafe import_and_reload strategy to widen the race window.

    - Pops the module from sys.modules (creating a gap for concurrent imports)
    - Sleeps briefly so other tasks can try to import submodules
    - Reimports and reloads the module, then restores sys.modules
    """
    sys.modules.pop(module_name, None)
    # Sleep to increase the chance that another coroutine attempts
    # to import the package or its submodules during this gap.
    # A slightly wider window makes the race much more likely in CI.
    time.sleep(0.05)
    module = importlib.import_module(module_name)
    reloaded = importlib.reload(module)
    sys.modules[module_name] = reloaded
    return reloaded


@pytest.mark.skip(
    reason="Flaky: race condition may not trigger reliably. "
    "The fix is validated by test_import_reload_no_race_with_lock."
)
@pytest.mark.anyio
async def test_import_reload_race_old_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demonstrate the race by restoring the old behavior under load.

    Expectations: With the bad importer, we should see at least one
    transient ModuleNotFoundError/KeyError during the test duration.
    """
    # Ensure base package is importable before we begin
    importlib.import_module("tracecat_registry")

    # Monkeypatch to the bad importer
    monkeypatch.setattr(
        "tracecat.registry.repository.import_and_reload",
        _bad_import_and_reload,
        raising=True,
    )

    errors: list[BaseException] = []

    async def reloader_task(duration_s: float = 1.0) -> None:
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            # Run in a thread to avoid blocking the event loop during time.sleep
            await asyncio.to_thread(_bad_import_and_reload, "tracecat_registry")

    async def importer_task(duration_s: float = 1.0) -> None:
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            try:
                # Try importing a submodule commonly referenced by actions
                # Force a fresh import by clearing the submodule cache entries
                # so importlib must resolve the parent package during the window.
                sys.modules.pop(
                    "tracecat_registry.integrations.crowdstrike_falconpy", None
                )
                sys.modules.pop("tracecat_registry.integrations", None)
                importlib.invalidate_caches()
                importlib.import_module(
                    "tracecat_registry.integrations.crowdstrike_falconpy"
                )
            except Exception as e:  # noqa: BLE001 - we collect all exceptions
                errors.append(e)
            await asyncio.sleep(0)

    # Drive both concurrently for a short duration
    await asyncio.gather(reloader_task(1.2), importer_task(1.2))

    # We expect at least one transient error under the bad importer
    assert any(isinstance(e, ModuleNotFoundError | KeyError) for e in errors), (
        f"Expected transient import errors, got: {errors!r}"
    )


@pytest.mark.anyio
async def test_import_reload_no_race_with_lock() -> None:
    """Verify the safe importer avoids transient errors under concurrency.

    Uses the current implementation of import_and_reload (with a process‑wide
    lock and no sys.modules pop). We concurrently reload the base package and
    import a submodule many times and assert no errors occur.
    """
    # Ensure base package is importable before we begin
    importlib.import_module("tracecat_registry")

    errors: list[BaseException] = []

    async def reloader_task(iterations: int = 200) -> None:
        for _ in range(iterations):
            # Run the safe reloader in a worker thread for true concurrency
            await asyncio.to_thread(safe_import_and_reload, "tracecat_registry")
            # Remove sleep to maximize collision chances
            await asyncio.sleep(0)

    async def importer_task(iterations: int = 2000) -> None:
        for _ in range(iterations):
            try:
                # Force a fresh import by clearing the submodule cache entries
                sys.modules.pop(
                    "tracecat_registry.integrations.crowdstrike_falconpy", None
                )
                sys.modules.pop("tracecat_registry.integrations", None)
                importlib.invalidate_caches()
                importlib.import_module(
                    "tracecat_registry.integrations.crowdstrike_falconpy"
                )
            except Exception as e:  # noqa: BLE001 - we collect all exceptions
                errors.append(e)
            # Remove sleep to maximize collision chances
            await asyncio.sleep(0)

    # Run more concurrent tasks to increase collision probability
    await asyncio.gather(
        reloader_task(200),
        reloader_task(200),
        importer_task(2000),
        importer_task(2000),
        importer_task(2000),
    )

    assert not errors, f"Unexpected import errors under safe reload: {errors!r}"


def test_import_reload_failure_keeps_module_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure a reload failure doesn't evict the module from sys.modules."""
    module_name = "temp_udf_mod_pop_race"
    module_path = tmp_path / f"{module_name}.py"
    module_path.write_text("value = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)

    reload_started = threading.Event()
    continue_event = threading.Event()
    errors: list[BaseException] = []

    def fail_reload(target: ModuleType) -> ModuleType:
        reload_started.set()
        assert continue_event.wait(2)
        raise ImportError("forced reload failure")

    monkeypatch.setattr(importlib, "reload", fail_reload, raising=True)

    def reloader() -> None:
        try:
            safe_import_and_reload(module_name)
        except Exception as e:  # noqa: BLE001 - regression guard
            errors.append(e)

    thread = threading.Thread(target=reloader)
    thread.start()

    assert reload_started.wait(2)
    assert sys.modules.get(module_name) is module
    continue_event.set()
    thread.join(timeout=2)

    assert not errors
    assert sys.modules.get(module_name) is module
    sys.modules.pop(module_name, None)


def test_import_reload_pop_window_can_raise_module_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: pop window can trigger ModuleNotFoundError for submodule imports."""
    module_name = "temp_udf_pkg_pop_mnf"
    submodule_name = f"{module_name}.submod"
    package_dir = tmp_path / module_name
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("value = 1\n")
    (package_dir / "submod.py").write_text("value = 2\n")

    original_sys_path = list(sys.path)
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.invalidate_caches()
        importlib.import_module(submodule_name)

        # Make the package unimportable from sys.path if it gets popped.
        sys.path[:] = [p for p in sys.path if p != str(tmp_path)]
        importlib.invalidate_caches()
        sys.modules.pop(submodule_name, None)

        def fail_reload(module: ModuleType) -> ModuleType:
            raise ImportError("forced reload failure")

        monkeypatch.setattr(importlib, "reload", fail_reload, raising=True)

        popped_event = threading.Event()
        continue_event = threading.Event()
        errors: list[BaseException] = []
        reloader_ident = threading.get_ident()
        real_import_module = importlib.import_module

        def gated_import_module(name: str, package: str | None = None) -> ModuleType:
            if name == module_name and threading.get_ident() == reloader_ident:
                popped_event.set()
                assert continue_event.wait(2)
            return real_import_module(name, package)

        monkeypatch.setattr(
            importlib, "import_module", gated_import_module, raising=True
        )

        def importer() -> None:
            popped_event.wait(2)
            try:
                importlib.import_module(submodule_name)
            except Exception as e:  # noqa: BLE001 - collect all errors for assertion
                errors.append(e)
            finally:
                continue_event.set()

        thread = threading.Thread(target=importer)
        thread.start()
        safe_import_and_reload(module_name)
        thread.join(timeout=2)

        assert not errors, f"Unexpected import errors: {errors!r}"
    finally:
        sys.path[:] = original_sys_path
        sys.modules.pop(submodule_name, None)
        sys.modules.pop(module_name, None)
