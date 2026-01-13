"""Adapter protocol for stream event conversion.

This module defines the interface for adapters that convert harness-native
stream events to unified stream events.

Message persistence is handled by ChatMessage (in chat/schemas.py) with raw
JSON data and harness tag - no conversion needed at the adapter level.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from tracecat.agent.common.stream_types import HarnessType, UnifiedStreamEvent


class BaseHarnessAdapter(ABC):
    """Abstract base class for harness adapters.

    Subclasses must implement the stream event conversion method.
    """

    harness_name: ClassVar[HarnessType]

    @abstractmethod
    def to_unified_event(self, native: Any) -> UnifiedStreamEvent:
        """Convert a harness-native stream event to unified format."""
        ...
