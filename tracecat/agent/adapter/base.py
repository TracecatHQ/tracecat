"""Adapter protocol for stream event conversion.

This module defines the interface for adapters that convert harness-native
stream events to unified stream events.

Message persistence is handled by ChatMessage (in chat/schemas.py) with raw
JSON data and harness tag - no conversion needed at the adapter level.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from tracecat.agent.stream.types import HarnessType, UnifiedStreamEvent


@runtime_checkable
class HarnessAdapter(Protocol):
    """Protocol for converting harness-native stream events to unified format."""

    @classmethod
    def harness_name(cls) -> HarnessType:
        """Identifier for this harness."""
        ...

    @classmethod
    def to_unified_event(cls, native: Any) -> UnifiedStreamEvent:
        """Convert a harness-native stream event to unified format.

        Args:
            native: The native event type (e.g., AgentStreamEvent)

        Returns:
            UnifiedStreamEvent representation
        """
        ...


class BaseHarnessAdapter(ABC):
    """Abstract base class for harness adapters.

    Subclasses must implement the stream event conversion method.
    """

    @classmethod
    @abstractmethod
    def harness_name(cls) -> HarnessType:
        """Identifier for this harness."""
        ...

    @classmethod
    @abstractmethod
    def to_unified_event(cls, native: Any) -> UnifiedStreamEvent:
        """Convert a harness-native stream event to unified format."""
        ...
