"""Link a run to the trigger that started it with ``TracecatTriggerId``.

The attribute is set at start, client side, never from workflow code, so
replays and runs started before it existed are unaffected.

Starting a run with an attribute the namespace does not have fails the start.
Registration can lag the code: API startup registers it in the background,
and Temporal Cloud namespaces are provisioned by hand. So each process sets the
attribute only after a visibility query on it has succeeded once. Temporal
rejects a query on an unknown attribute, and the query needs no operator
access, which Cloud runtime keys may lack. Until then runs start without it,
exactly as before.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio.client import Client
from temporalio.common import SearchAttributePair

from tracecat.logger import logger
from tracecat.workflow.executions.enums import TemporalSearchAttr

PROBE_RETRY_SECONDS = 60.0
"""How long a process waits to probe again after the attribute was missing."""
_PROBE_RPC_TIMEOUT = timedelta(seconds=5)
_PROBE_QUERY = f"{TemporalSearchAttr.TRIGGER_ID.value} = '{uuid.UUID(int=0)}'"


@dataclass(slots=True)
class _ProbeState:
    ready: bool = False
    next_probe_at: float = 0.0
    # Concurrent first starts wait for the one probe instead of starting
    # without the attribute while it runs.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_probe = _ProbeState()


async def trigger_id_registered(client: Client) -> bool:
    """Whether the namespace has ``TracecatTriggerId``; cached once true."""
    if _probe.ready:
        return True
    async with _probe.lock:
        if _probe.ready:
            return True
        now = time.monotonic()
        if now < _probe.next_probe_at:
            return False
        try:
            await client.count_workflows(_PROBE_QUERY, rpc_timeout=_PROBE_RPC_TIMEOUT)
        except Exception as e:
            _probe.next_probe_at = time.monotonic() + PROBE_RETRY_SECONDS
            logger.warning(
                "TracecatTriggerId is not usable yet; trigger runs start without it",
                error_type=type(e).__name__,
                retry_in_seconds=PROBE_RETRY_SECONDS,
            )
            return False
        _probe.ready = True
        return True


async def trigger_id_pairs(
    client: Client, trigger_id: uuid.UUID | None
) -> list[SearchAttributePair[Any]]:
    """The ``TracecatTriggerId`` pair for a start, or none.

    None when the run has no trigger or the namespace lacks the attribute.
    """
    if trigger_id is None or not await trigger_id_registered(client):
        return []
    return [TemporalSearchAttr.TRIGGER_ID.create_pair(str(trigger_id))]
