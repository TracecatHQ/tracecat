"""Sandboxed agent runtime utilities.

This package provides protocol models and I/O utilities for running
agent runtimes in isolated (NSJail) sandboxes without database access.
"""

from tracecat.agent.sandbox.protocol import (
    ApprovalContinuationPayload,
    ApprovalContinuationPayloadTA,
    RuntimeEventEnvelope,
    RuntimeEventEnvelopeTA,
    RuntimeInitPayload,
    RuntimeInitPayloadTA,
)
from tracecat.agent.sandbox.socket_io import SocketStreamWriter

__all__ = [
    "ApprovalContinuationPayload",
    "ApprovalContinuationPayloadTA",
    "RuntimeEventEnvelope",
    "RuntimeEventEnvelopeTA",
    "RuntimeInitPayload",
    "RuntimeInitPayloadTA",
    "SocketStreamWriter",
]
