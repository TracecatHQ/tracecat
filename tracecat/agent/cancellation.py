"""Out-of-band user-cancel signalling for agent turns.

Temporal only delivers activity cancellation to a running activity via
heartbeat RPC responses, and the SDK throttles outbound heartbeats to a
fraction of the heartbeat timeout. A short agent turn can therefore finish
normally before the executor ever learns that ``ActivityTaskCancelRequested``
was recorded - especially with a single executor activity slot in local dev.

To make stop reliably live, the API writes a Redis signal keyed by the turn's
run id (the ``curr_run_id`` workflow token) when the user cancels, and the
executor activity polls it directly while the turn runs. The Temporal
cancellation path is kept as a fallback; this signal is the low-latency
primary.
"""

from __future__ import annotations

from tracecat.redis.client import get_redis_client

TURN_CANCEL_SIGNAL_TTL_SECONDS = 600
"""Signal expiry. Keys are scoped to a single turn's run id, so the TTL only
bounds Redis growth for turns that end before the signal is consumed."""

TURN_CANCEL_POLL_INTERVAL_SECONDS = 0.5
"""How often the executor activity polls for a pending cancel signal."""


def turn_cancel_key(run_id: str) -> str:
    """Redis key holding the pending cancel reason for one agent turn."""
    return f"agent:turn-cancel:{run_id}"


async def signal_turn_cancel(run_id: str, *, reason: str) -> None:
    """Record a user cancel request for the turn running under run_id."""
    client = await get_redis_client()
    await client.set(
        turn_cancel_key(run_id),
        reason,
        expire_seconds=TURN_CANCEL_SIGNAL_TTL_SECONDS,
    )


async def read_turn_cancel_signal(run_id: str) -> str | None:
    """Return the pending cancel reason for run_id, if one was signalled."""
    client = await get_redis_client()
    return await client.get(turn_cancel_key(run_id))
