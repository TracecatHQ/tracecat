"""Event-loop lag sampler."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from tracecat.observability.loop_lag import LoopLagSampler


@pytest.mark.anyio
async def test_not_started_has_no_reading() -> None:
    assert LoopLagSampler().max_lag_seconds() is None


@pytest.mark.anyio
async def test_idle_loop_reads_low_lag() -> None:
    sampler = LoopLagSampler(tick_seconds=0.01, window_seconds=0.5)
    sampler.start()
    try:
        await asyncio.sleep(0.1)
        lag = sampler.max_lag_seconds()
        assert lag is not None
        # Loose: well under the 0.25 s overload threshold, safe on a busy runner.
        assert lag < 0.2
    finally:
        await sampler.stop()
    assert sampler.max_lag_seconds() is None


@pytest.mark.anyio
async def test_blocked_loop_reads_high_lag_now_and_after() -> None:
    sampler = LoopLagSampler(tick_seconds=0.01, window_seconds=0.5)
    sampler.start()
    try:
        await asyncio.sleep(0.05)
        time.sleep(0.3)  # Block the loop, as CPU-bound work would.
        # Overdue right now, before the sampler has run again.
        lag = sampler.max_lag_seconds()
        assert lag is not None
        assert lag >= 0.25
        await asyncio.sleep(0.05)
        # Recorded in the window once the sampler wakes.
        lag = sampler.max_lag_seconds()
        assert lag is not None
        assert lag >= 0.25
    finally:
        await sampler.stop()


@pytest.mark.anyio
async def test_lag_ages_out_of_the_window() -> None:
    sampler = LoopLagSampler(tick_seconds=0.01, window_seconds=0.1)
    sampler.start()
    try:
        await asyncio.sleep(0.03)
        time.sleep(0.4)
        await asyncio.sleep(0.3)
        lag = sampler.max_lag_seconds()
        assert lag is not None
        assert lag < 0.2
    finally:
        await sampler.stop()


@pytest.mark.anyio
async def test_gauge_reports_a_stall_in_progress() -> None:
    sampler = LoopLagSampler(tick_seconds=0.01, window_seconds=0.5)
    sampler.start()
    try:
        await asyncio.sleep(0.05)
        time.sleep(0.3)
        # The exporter reads while the sampler hasn't woken up yet.
        (observation,) = sampler._observe(MagicMock())
        assert observation.value >= 0.25
    finally:
        await sampler.stop()
    assert list(sampler._observe(MagicMock())) == []
